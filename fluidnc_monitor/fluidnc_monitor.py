#!/usr/bin/env python3
"""
FluidNC/Grbl_ESP32 → MQTT монитор для Home Assistant
- laser      (192.168.1.105) FluidNC v3.9.9  — WebSocket порт 81
- laser_mini (192.168.1.107) Grbl_ESP32 1.3a — Telnet порт 23
Парсит GRBL-статус: <Idle|MPos:x,y,z,a|FS:feed,spindle|...>
"""

import asyncio
import websockets
import paho.mqtt.client as mqtt
import json
import logging
import signal
import sys
from typing import Optional

# ─────────────────────────────────────────────
# КОНФИГУРАЦИЯ — меняй только здесь
# ─────────────────────────────────────────────

LASERS = [
    {
        "name":      "laser",           # ID для MQTT топиков
        "host":      "192.168.1.105",
        "protocol":  "websocket",       # FluidNC v3.9.9
        "ws_port":   81,
    },
    {
        "name":      "laser_mini",
        "host":      "192.168.1.107",
        "protocol":  "telnet",          # Grbl_ESP32 1.3a
        "telnet_port": 23,
    },
]

MQTT_HOST     = "localhost"   # Mosquitto внутри HA
MQTT_PORT     = 1883
MQTT_USER     = ""            # если настроена авторизация в Mosquitto
MQTT_PASSWORD = ""
MQTT_PREFIX   = "fluidnc"    # базовый префикс топиков

# Как часто запрашивать статус (секунды)
POLL_INTERVAL = 2.0

# ─────────────────────────────────────────────
# Логирование
# ─────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)


# ─────────────────────────────────────────────
# Парсер GRBL-статуса
# ─────────────────────────────────────────────

# Словарь человекочитаемых статусов
STATE_MAP = {
    "Idle":  "Ожидание",
    "Run":   "Работает",
    "Hold":  "Пауза",
    "Jog":   "Позиционирование",
    "Alarm": "Авария",
    "Door":  "Дверь открыта",
    "Check": "Проверка",
    "Home":  "Наезд на HOME",
    "Sleep": "Сон",
}

def parse_grbl_status(raw: str) -> Optional[dict]:
    """
    Парсит строку вида:
    <Idle|MPos:0.000,0.000,0.000,0.000|FS:0,0|Pn:A|WCO:0.000,0.000,0.000,0.000>
    Возвращает словарь или None если строка не похожа на статус.
    """
    raw = raw.strip()
    if not raw.startswith("<") or not raw.endswith(">"):
        return None

    content = raw[1:-1]
    parts = content.split("|")

    result = {
        "raw": raw,
        "state": parts[0] if parts else "Unknown",
        "state_ru": STATE_MAP.get(parts[0], parts[0]) if parts else "Unknown",
        "mpos_x": 0.0, "mpos_y": 0.0, "mpos_z": 0.0, "mpos_a": 0.0,
        "feed": 0, "spindle": 0,
        "laser_on": False,
        "pins": "",
        "job_running": False,
    }

    for part in parts[1:]:
        if part.startswith("MPos:"):
            coords = part[5:].split(",")
            keys = ["mpos_x", "mpos_y", "mpos_z", "mpos_a"]
            for i, k in enumerate(keys):
                if i < len(coords):
                    try:
                        result[k] = float(coords[i])
                    except ValueError:
                        pass

        elif part.startswith("FS:"):
            fs = part[3:].split(",")
            try:
                result["feed"]    = int(float(fs[0])) if len(fs) > 0 else 0
                result["spindle"] = int(float(fs[1])) if len(fs) > 1 else 0
            except ValueError:
                pass
            # Лазер активен если spindle > 0
            result["laser_on"] = result["spindle"] > 0

        elif part.startswith("Pn:"):
            result["pins"] = part[3:]

    result["job_running"] = result["state"] == "Run"

    return result


# ─────────────────────────────────────────────
# MQTT хелперы
# ─────────────────────────────────────────────

def build_topics(name: str) -> dict:
    base = f"{MQTT_PREFIX}/{name}"
    return {
        "state":       f"{base}/state",        # Idle / Run / Hold …
        "state_ru":    f"{base}/state_ru",      # Ожидание / Работает …
        "laser_on":    f"{base}/laser_on",      # ON / OFF
        "job_running": f"{base}/job_running",   # ON / OFF
        "feed":        f"{base}/feed",          # мм/мин
        "spindle":     f"{base}/spindle",       # мощность лазера
        "mpos_x":      f"{base}/mpos_x",
        "mpos_y":      f"{base}/mpos_y",
        "mpos_z":      f"{base}/mpos_z",
        "mpos_a":      f"{base}/mpos_a",
        "pins":        f"{base}/pins",
        "available":   f"{base}/available",     # online / offline
        "json":        f"{base}/json",          # полный JSON для отладки
    }


def publish_status(client: mqtt.Client, name: str, data: dict):
    topics = build_topics(name)
    pub = client.publish

    pub(topics["state"],       data["state"],               retain=True)
    pub(topics["state_ru"],    data["state_ru"],             retain=True)
    pub(topics["laser_on"],    "ON" if data["laser_on"] else "OFF", retain=True)
    pub(topics["job_running"], "ON" if data["job_running"] else "OFF", retain=True)
    pub(topics["feed"],        str(data["feed"]),            retain=True)
    pub(topics["spindle"],     str(data["spindle"]),         retain=True)
    pub(topics["mpos_x"],      f"{data['mpos_x']:.3f}",     retain=True)
    pub(topics["mpos_y"],      f"{data['mpos_y']:.3f}",     retain=True)
    pub(topics["mpos_z"],      f"{data['mpos_z']:.3f}",     retain=True)
    pub(topics["mpos_a"],      f"{data['mpos_a']:.3f}",     retain=True)
    pub(topics["pins"],        data["pins"],                 retain=True)
    pub(topics["json"],        json.dumps(data, ensure_ascii=False), retain=True)


def publish_availability(client: mqtt.Client, name: str, online: bool):
    topics = build_topics(name)
    payload = "online" if online else "offline"
    client.publish(topics["available"], payload, retain=True)
    logging.getLogger(name).info(f"Availability → {payload}")


# ─────────────────────────────────────────────
# WebSocket воркер (FluidNC v3+)
# ─────────────────────────────────────────────

async def websocket_worker(laser: dict, mqtt_client: mqtt.Client):
    name = laser["name"]
    host = laser["host"]
    port = laser["ws_port"]
    uri  = f"ws://{host}:{port}"
    log  = logging.getLogger(name)
    # Watchdog — если нет данных дольше этого времени, переподключаемся
    WATCHDOG_TIMEOUT = 60.0

    while True:
        try:
            log.info(f"WS: Подключаемся к {uri} ...")
            async with websockets.connect(
                uri,
                ping_interval=20,
                ping_timeout=10,
                open_timeout=10,
            ) as ws:
                log.info("WS: Подключено!")
                publish_availability(mqtt_client, name, True)

                last_data = asyncio.get_event_loop().time()

                async def sender():
                    while True:
                        await ws.send("?")
                        await asyncio.sleep(POLL_INTERVAL)

                async def receiver():
                    nonlocal last_data
                    async for message in ws:
                        if isinstance(message, bytes):
                            text = message.decode("utf-8", errors="replace")
                        else:
                            text = message
                        parsed = parse_grbl_status(text)
                        if parsed:
                            last_data = asyncio.get_event_loop().time()
                            log.info(f"Status: {parsed['state']} | laser={'ON' if parsed['laser_on'] else 'OFF'} | feed={parsed['feed']}")
                            publish_status(mqtt_client, name, parsed)

                async def watchdog():
                    nonlocal last_data
                    while True:
                        await asyncio.sleep(10)
                        elapsed = asyncio.get_event_loop().time() - last_data
                        if elapsed > WATCHDOG_TIMEOUT:
                            log.warning(f"WS: Нет данных {elapsed:.0f} сек — принудительное переподключение")
                            await ws.close()
                            return

                await asyncio.gather(sender(), receiver(), watchdog())

        except (websockets.exceptions.ConnectionClosed,
                websockets.exceptions.WebSocketException,
                OSError, asyncio.TimeoutError) as e:
            log.warning(f"WS: Соединение потеряно: {e}. Повтор через 10 сек...")
        except Exception as e:
            log.error(f"WS: Неожиданная ошибка: {e}. Повтор через 10 сек...")
        finally:
            publish_availability(mqtt_client, name, False)
            await asyncio.sleep(10)


# ─────────────────────────────────────────────
# Telnet воркер (Grbl_ESP32 1.3a)
# ─────────────────────────────────────────────

async def telnet_worker(laser: dict, mqtt_client: mqtt.Client):
    name = laser["name"]
    host = laser["host"]
    port = laser["telnet_port"]
    log  = logging.getLogger(name)

    while True:
        reader = None
        writer = None
        try:
            log.info(f"Telnet: Подключаемся к {host}:{port} ...")
            reader, writer = await asyncio.wait_for(
                asyncio.open_connection(host, port),
                timeout=10,
            )
            log.info("Telnet: Подключено!")
            publish_availability(mqtt_client, name, True)

            # Читаем приветствие (Grbl\n)
            await asyncio.wait_for(reader.readline(), timeout=5)

            while True:
                # Отправляем запрос статуса
                writer.write(b"?\n")
                await writer.drain()

                # Читаем ответы до таймаута
                deadline = asyncio.get_event_loop().time() + POLL_INTERVAL
                while asyncio.get_event_loop().time() < deadline:
                    try:
                        line = await asyncio.wait_for(
                            reader.readline(),
                            timeout=max(0.1, deadline - asyncio.get_event_loop().time()),
                        )
                        if not line:
                            raise ConnectionResetError("Соединение закрыто сервером")

                        text = line.decode("utf-8", errors="replace").strip()
                        parsed = parse_grbl_status(text)
                        if parsed:
                            log.info(f"Status: {parsed['state']} | laser={'ON' if parsed['laser_on'] else 'OFF'} | feed={parsed['feed']}")
                            publish_status(mqtt_client, name, parsed)

                    except asyncio.TimeoutError:
                        break

        except (OSError, asyncio.TimeoutError, ConnectionResetError) as e:
            log.warning(f"Telnet: Соединение потеряно: {e}. Повтор через 10 сек...")
        except Exception as e:
            log.error(f"Telnet: Неожиданная ошибка: {e}. Повтор через 10 сек...")
        finally:
            if writer:
                try:
                    writer.close()
                    await writer.wait_closed()
                except Exception:
                    pass
            publish_availability(mqtt_client, name, False)
            await asyncio.sleep(10)


# ─────────────────────────────────────────────
# Диспетчер — выбирает нужный воркер
# ─────────────────────────────────────────────

async def laser_worker(laser: dict, mqtt_client: mqtt.Client):
    protocol = laser.get("protocol", "websocket")
    if protocol == "telnet":
        await telnet_worker(laser, mqtt_client)
    else:
        await websocket_worker(laser, mqtt_client)


# ─────────────────────────────────────────────
# Точка входа
# ─────────────────────────────────────────────

async def main():
    log = logging.getLogger("main")

    # Подключаемся к MQTT
    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="fluidnc_monitor", clean_session=True)
    if MQTT_USER:
        client.username_pw_set(MQTT_USER, MQTT_PASSWORD)

    # LWT — если скрипт упадёт, HA узнает
    for laser in LASERS:
        topics = build_topics(laser["name"])
        client.will_set(topics["available"], "offline", retain=True)

    client.connect(MQTT_HOST, MQTT_PORT, keepalive=60)
    client.loop_start()
    log.info(f"MQTT подключён к {MQTT_HOST}:{MQTT_PORT}")

    # Graceful shutdown
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, lambda: asyncio.create_task(shutdown(client)))

    # Запускаем воркеры для всех лазеров параллельно
    await asyncio.gather(*[
        laser_worker(laser, client) for laser in LASERS
    ])


async def shutdown(mqtt_client: mqtt.Client):
    log = logging.getLogger("main")
    log.info("Завершение работы...")
    for laser in LASERS:
        publish_availability(mqtt_client, laser["name"], False)
    mqtt_client.loop_stop()
    mqtt_client.disconnect()
    sys.exit(0)


if __name__ == "__main__":
    asyncio.run(main())
