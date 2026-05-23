# FluidNC Monitor — Home Assistant Addon

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)

Home Assistant аддон для мониторинга лазерных станков с прошивкой **FluidNC** и **Grbl_ESP32** через MQTT.

## Установка

1. Перейдите в **Настройки → Аддоны → Магазин аддонов**
2. Нажмите ⋮ → **Репозитории**
3. Добавьте: `https://github.com/PhantoMM78/hassio-fluidnc-monitor`
4. Найдите **FluidNC Monitor** и нажмите **Установить**
5. Перейдите на вкладку **Конфигурация** и укажите параметры
6. Нажмите **Запустить** и включите **Автозапуск**

## Возможности

- Подключение к **FluidNC v3+** через WebSocket
- Подключение к **Grbl_ESP32** (старые прошивки) через Telnet
- Поддержка **нескольких лазеров** одновременно
- Публикация статусов в **MQTT** → сенсоры в Home Assistant
- Автоматическое переподключение при обрыве связи
- Watchdog: принудительное переподключение если нет данных 60 секунд
- Настройка через UI аддона без редактирования файлов

## Конфигурация

```yaml
mqtt_host: core-mosquitto    # хост MQTT брокера
mqtt_port: 1883              # порт MQTT
mqtt_user: ""                # логин (если требуется)
mqtt_password: ""            # пароль (если требуется)
mqtt_prefix: fluidnc         # префикс MQTT топиков
poll_interval: 2.0           # интервал опроса в секундах

lasers:
  - name: laser              # уникальное имя (используется в топиках)
    host: 192.168.1.105      # IP адрес лазера
    protocol: websocket      # websocket (FluidNC v3+) или telnet (Grbl_ESP32)
    port: 81                 # порт (81 для WebSocket, 23 для Telnet)

  - name: laser_mini
    host: 192.168.1.107
    protocol: telnet
    port: 23
```

## MQTT топики

Для каждого лазера создаются следующие топики:

| Топик | Описание | Значения |
|-------|----------|----------|
| `fluidnc/{name}/state` | Статус станка | Idle, Run, Hold, Alarm |
| `fluidnc/{name}/job_running` | Выполняется задание | ON / OFF |
| `fluidnc/{name}/laser_on` | Лазерный луч активен | ON / OFF |
| `fluidnc/{name}/spindle` | Мощность лазера | число |
| `fluidnc/{name}/feed` | Скорость подачи | мм/мин |
| `fluidnc/{name}/mpos_x` | Позиция X | мм |
| `fluidnc/{name}/mpos_y` | Позиция Y | мм |
| `fluidnc/{name}/mpos_z` | Позиция Z | мм |
| `fluidnc/{name}/available` | Доступность | online / offline |
| `fluidnc/{name}/json` | Полный статус | JSON |

## Сенсоры в Home Assistant

Добавьте в `configuration.yaml` или отдельный пакет в `/config/conf/`:

```yaml
mqtt:
  sensor:
    - name: "Laser Статус"
      unique_id: laser_state
      state_topic: "fluidnc/laser/state"
      availability_topic: "fluidnc/laser/available"
      payload_available: "online"
      payload_not_available: "offline"
      icon: mdi:laser-pointer

    - name: "Laser Мощность"
      unique_id: laser_spindle
      state_topic: "fluidnc/laser/spindle"
      availability_topic: "fluidnc/laser/available"
      payload_available: "online"
      payload_not_available: "offline"
      icon: mdi:brightness-7

    - name: "Laser Скорость подачи"
      unique_id: laser_feed
      state_topic: "fluidnc/laser/feed"
      availability_topic: "fluidnc/laser/available"
      payload_available: "online"
      payload_not_available: "offline"
      unit_of_measurement: "мм/мин"
      icon: mdi:speedometer

    - name: "Laser Pos X"
      unique_id: laser_pos_x
      state_topic: "fluidnc/laser/mpos_x"
      availability_topic: "fluidnc/laser/available"
      payload_available: "online"
      payload_not_available: "offline"
      unit_of_measurement: "мм"

    - name: "Laser Pos Y"
      unique_id: laser_pos_y
      state_topic: "fluidnc/laser/mpos_y"
      availability_topic: "fluidnc/laser/available"
      payload_available: "online"
      payload_not_available: "offline"
      unit_of_measurement: "мм"

  binary_sensor:
    - name: "Laser Работает"
      unique_id: laser_job_running
      state_topic: "fluidnc/laser/job_running"
      availability_topic: "fluidnc/laser/available"
      payload_available: "online"
      payload_not_available: "offline"
      payload_on: "ON"
      payload_off: "OFF"
      device_class: running

    - name: "Laser Активен"
      unique_id: laser_laser_on
      state_topic: "fluidnc/laser/laser_on"
      availability_topic: "fluidnc/laser/available"
      payload_available: "online"
      payload_not_available: "offline"
      payload_on: "ON"
      payload_off: "OFF"
      device_class: running
```

## Пример автоматизации вытяжки

```yaml
- alias: "Лазер — Вытяжка ВКЛ"
  mode: single
  max_exceeded: silent
  trigger:
    - platform: state
      entity_id:
        - binary_sensor.laser_rabotaet
        - binary_sensor.laser_mini_rabotaet
      to: "on"
      for: "00:00:05"
  action:
    - action: switch.turn_on
      target:
        entity_id: switch.vytiazhka

- alias: "Лазер — Вытяжка ВЫКЛ через 5 минут"
  mode: restart
  trigger:
    - platform: state
      entity_id:
        - binary_sensor.laser_rabotaet
        - binary_sensor.laser_mini_rabotaet
      to: "off"
      for: "00:00:10"
  condition:
    - condition: template
      value_template: "{{ states('binary_sensor.laser_rabotaet') != 'on' }}"
    - condition: template
      value_template: "{{ states('binary_sensor.laser_mini_rabotaet') != 'on' }}"
  action:
    - delay: "00:05:00"
    - condition: template
      value_template: "{{ states('binary_sensor.laser_rabotaet') != 'on' }}"
    - condition: template
      value_template: "{{ states('binary_sensor.laser_mini_rabotaet') != 'on' }}"
    - action: switch.turn_off
      target:
        entity_id: switch.vytiazhka
```

## Совместимость

| Прошивка | Протокол | Версия |
|----------|----------|--------|
| FluidNC | WebSocket | v3.x+ |
| Grbl_ESP32 | Telnet | 1.3a+ |
| GRBL | Telnet | любая с Telnet |

## Лицензия

MIT
