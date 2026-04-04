#!/bin/bash
# Activate the virtual environment
source venv/bin/activate

# Run the automation in daemon mode (continuous watching)
echo "Starting Invoice Automation..."
python src/main.py --daemon
