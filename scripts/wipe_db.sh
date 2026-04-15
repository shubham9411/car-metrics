#!/bin/bash

# WARNING: This script will DESTROY the entire car-metrics database,
# including all real trips, mock trips, routines, and telemetry.

echo "⚠️  WARNING: You are about to permanently delete the entire car_metrics.db database."
read -p "Are you absolutely sure? (y/N) " -n 1 -r
echo
if [[ ! $REPLY =~ ^[Yy]$ ]]
then
    echo "Aborted."
    exit 1
fi

echo "🛑 Stopping services..."
sudo systemctl stop car-metrics car-metrics-web

echo "🗑️  Deleting database file..."
rm -f /home/dietpi/car-metrics-data/car_metrics.db

echo "🚀 Restarting services (creates a fresh database schema)..."
sudo systemctl start car-metrics car-metrics-web

# Wait a second for the DB to be created and init to run
sleep 2 

echo "✅ Done! Database has been completely wiped and recreated."
echo "If you want to test mock telemetry from scratch, run:"
echo "touch /home/dietpi/car-metrics-data/.simulate_data"
