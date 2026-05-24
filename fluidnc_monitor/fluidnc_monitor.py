#!/usr/bin/env python3
"""
FluidNC/Grbl_ESP32 → MQTT монитор для Home Assistant
Настраивается через опции аддона HA
"""

import asyncio
import websockets
import paho.mqtt.client as mqtt
import json
import logging
import signal
import sys
import os
from typing import Optional

# ─────────────────────────────────────────────
# КОНФИГУРАЦИЯ — читается из переменных окружения (аддон HA)
# или из значений по умолчанию
# ─────────────────────────────────────────────

MQTT_HOST     = os.environ.get("MQTT_HOST",     "core-mosquitto")
MQTT_PORT     = int(os.environ.get("MQTT_PORT", "1883"))
MQTT_USER     = os.environ.get("MQTT_USER",     "")
MQTT_PASSWORD = os.environ.get("MQTT_PASSWORD", "")
MQTT_PREFIX   = os.environ.get("MQTT_PREFIX",   "fluidnc")
POLL_INTERVAL = float(os.environ.get("POLL_INTERVAL", "2.0"))

# Лазеры из JSON переменной или дефолт
_lasers_json = os.environ.get("LASERS_JSON", "")
if _lasers_json:
    LASERS = json.loads(_lasers_json)
else:
    LASERS = [
        {"name": "laser",      "host": "192.168.1.105", "protocol": "websocket", "port": 81},
        {"name": "laser_mini", "host": "192.168.1.107", "protocol": "telnet",    "port": 23},
    ]

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
            for i, k in enumerate(["mpos_x", "mpos_y", "mpos_z", "mpos_a"]):
                if i < len(coords):
                    try: result[k] = float(coords[i])
                    except ValueError: pass
        elif part.startswith("FS:"):
            fs = part[3:].split(",")
            try:
                result["feed"]    = int(float(fs[0])) if fs else 0
                result["spindle"] = int(float(fs[1])) if len(fs) > 1 else 0
            except ValueError: pass
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
        "state":       f"{base}/state",
        "laser_on":    f"{base}/laser_on",
        "job_running": f"{base}/job_running",
        "feed":        f"{base}/feed",
        "spindle":     f"{base}/spindle",
        "mpos_x":      f"{base}/mpos_x",
        "mpos_y":      f"{base}/mpos_y",
        "mpos_z":      f"{base}/mpos_z",
        "mpos_a":      f"{base}/mpos_a",
        "pins":        f"{base}/pins",
        "available":   f"{base}/available",
        "json":        f"{base}/json",
    }

def publish_status(client: mqtt.Client, name: str, data: dict):
    t = build_topics(name)
    p = client.publish
    p(t["state"],       data["state"],                          retain=True)
    p(t["laser_on"],    "ON" if data["laser_on"] else "OFF",    retain=True)
    p(t["job_running"], "ON" if data["job_running"] else "OFF", retain=True)
    p(t["feed"],        str(data["feed"]),                      retain=True)
    p(t["spindle"],     str(data["spindle"]),                   retain=True)
    p(t["mpos_x"],      f"{data['mpos_x']:.3f}",               retain=True)
    p(t["mpos_y"],      f"{data['mpos_y']:.3f}",               retain=True)
    p(t["mpos_z"],      f"{data['mpos_z']:.3f}",               retain=True)
    p(t["mpos_a"],      f"{data['mpos_a']:.3f}",               retain=True)
    p(t["pins"],        data["pins"],                           retain=True)
    p(t["json"],        json.dumps(data, ensure_ascii=False),   retain=True)

def publish_availability(client: mqtt.Client, name: str, online: bool):
    payload = "online" if online else "offline"
    client.publish(build_topics(name)["available"], payload, retain=True)
    logging.getLogger(name).info(f"Availability → {payload}")

# ─────────────────────────────────────────────
# WebSocket воркер (FluidNC v3+)
# ─────────────────────────────────────────────

async def websocket_worker(laser: dict, mqtt_client: mqtt.Client):
    name = laser["name"]
    host = laser["host"]
    port = laser.get("port", 81)
    uri  = f"ws://{host}:{port}"
    log  = logging.getLogger(name)
    WATCHDOG_TIMEOUT = 60.0

    while True:
        try:
            log.info(f"WS: Подключаемся к {uri} ...")
            async with websockets.connect(
                uri, ping_interval=20, ping_timeout=10, open_timeout=10,
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
                        text = message.decode("utf-8", errors="replace") if isinstance(message, bytes) else message
                        parsed = parse_grbl_status(text)
                        if parsed:
                            last_data = asyncio.get_event_loop().time()
                            log.info(f"Status: {parsed['state']} | laser={'ON' if parsed['laser_on'] else 'OFF'} | feed={parsed['feed']}")
                            publish_status(mqtt_client, name, parsed)

                async def watchdog():
                    nonlocal last_data
                    while True:
                        await asyncio.sleep(10)
                        if asyncio.get_event_loop().time() - last_data > WATCHDOG_TIMEOUT:
                            log.warning(f"WS: Нет данных {WATCHDOG_TIMEOUT}с — переподключение")
                            await ws.close()
                            return

                await asyncio.gather(sender(), receiver(), watchdog())

        except Exception as e:
            log.warning(f"WS: Ошибка: {e}. Повтор через 10 сек...")
        finally:
            publish_availability(mqtt_client, name, False)
            await asyncio.sleep(10)

# ─────────────────────────────────────────────
# Telnet воркер (Grbl_ESP32 1.3a)
# ─────────────────────────────────────────────

async def telnet_worker(laser: dict, mqtt_client: mqtt.Client):
    name = laser["name"]
    host = laser["host"]
    port = laser.get("port", 23)
    log  = logging.getLogger(name)

    while True:
        reader = writer = None
        try:
            log.info(f"Telnet: Подключаемся к {host}:{port} ...")
            reader, writer = await asyncio.wait_for(
                asyncio.open_connection(host, port), timeout=10,
            )
            log.info("Telnet: Подключено!")
            publish_availability(mqtt_client, name, True)
            await asyncio.wait_for(reader.readline(), timeout=5)

            while True:
                writer.write(b"?\n")
                await writer.drain()
                deadline = asyncio.get_event_loop().time() + POLL_INTERVAL
                while asyncio.get_event_loop().time() < deadline:
                    try:
                        line = await asyncio.wait_for(
                            reader.readline(),
                            timeout=max(0.1, deadline - asyncio.get_event_loop().time()),
                        )
                        if not line:
                            raise ConnectionResetError("closed")
                        parsed = parse_grbl_status(line.decode("utf-8", errors="replace").strip())
                        if parsed:
                            log.info(f"Status: {parsed['state']} | laser={'ON' if parsed['laser_on'] else 'OFF'} | feed={parsed['feed']}")
                            publish_status(mqtt_client, name, parsed)
                    except asyncio.TimeoutError:
                        break

        except Exception as e:
            log.warning(f"Telnet: Ошибка: {e}. Повтор через 10 сек...")
        finally:
            if writer:
                try: writer.close(); await writer.wait_closed()
                except: pass
            publish_availability(mqtt_client, name, False)
            await asyncio.sleep(10)

# ─────────────────────────────────────────────
# Диспетчер
# ─────────────────────────────────────────────

async def laser_worker(laser: dict, mqtt_client: mqtt.Client):
    if laser.get("protocol") == "telnet":
        await telnet_worker(laser, mqtt_client)
    else:
        await websocket_worker(laser, mqtt_client)

# ─────────────────────────────────────────────
# Точка входа
# ─────────────────────────────────────────────

async def main():
    log = logging.getLogger("main")

    log.info(f"Конфигурация:")
    log.info(f"  MQTT: {MQTT_HOST}:{MQTT_PORT} user={MQTT_USER or '(нет)'}")
    log.info(f"  Prefix: {MQTT_PREFIX}")
    log.info(f"  Poll interval: {POLL_INTERVAL}s")
    for l in LASERS:
        log.info(f"  Лазер: {l['name']} {l['host']}:{l.get('port')} ({l.get('protocol','websocket')})")

    client = mqtt.Client(client_id="fluidnc_monitor", clean_session=True)
    if MQTT_USER:
        client.username_pw_set(MQTT_USER, MQTT_PASSWORD)
    for laser in LASERS:
        client.will_set(build_topics(laser["name"])["available"], "offline", retain=True)
    client.connect(MQTT_HOST, MQTT_PORT, keepalive=60)
    client.loop_start()
    log.info(f"MQTT подключён к {MQTT_HOST}:{MQTT_PORT}")

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, lambda: asyncio.create_task(shutdown(client)))

    await asyncio.gather(*[laser_worker(laser, client) for laser in LASERS])

async def shutdown(mqtt_client: mqtt.Client):
    for laser in LASERS:
        publish_availability(mqtt_client, laser["name"], False)
    mqtt_client.loop_stop()
    mqtt_client.disconnect()
    sys.exit(0)

if __name__ == "__main__":
    asyncio.run(main())
