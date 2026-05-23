#!/bin/sh
echo "FluidNC Monitor starting..."
CONFIG_PATH=/data/options.json
export MQTT_HOST=$(jq -r '.mqtt_host' $CONFIG_PATH)
export MQTT_PORT=$(jq -r '.mqtt_port' $CONFIG_PATH)
export MQTT_USER=$(jq -r '.mqtt_user' $CONFIG_PATH)
export MQTT_PASSWORD=$(jq -r '.mqtt_password' $CONFIG_PATH)
export MQTT_PREFIX=$(jq -r '.mqtt_prefix' $CONFIG_PATH)
export POLL_INTERVAL=$(jq -r '.poll_interval' $CONFIG_PATH)
export LASERS_JSON=$(jq -c '.lasers' $CONFIG_PATH)
exec python3 /fluidnc_monitor.py
