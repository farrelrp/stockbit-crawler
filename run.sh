#!/bin/bash
# Quick start script for Stockbit Running Trade Scraper

echo "======================================"
echo "Stockbit Running Trade Scraper"
echo "✨ Now with Selenium Automation!"
echo "======================================"
echo ""

# Check if Python is installed
if ! command -v python3 &> /dev/null; then
    echo "Error: Python 3 is not installed."
    echo "Please install Python 3 first."
    exit 1
fi

echo "✓ Python 3 found"

# Check if virtual environment exists
if [ ! -d "venv" ]; then
    echo ""
    echo "Creating virtual environment..."
    python3 -m venv venv
    echo "✓ Virtual environment created"
fi

# Activate virtual environment
echo ""
echo "Activating virtual environment..."
source venv/bin/activate

# Install dependencies
echo ""
echo "Installing dependencies (including Selenium & ChromeDriver)..."
echo "First run may take ~30 seconds to download ChromeDriver..."
pip install -q -r requirements.txt
echo "✓ Dependencies installed"

# Create necessary directories
mkdir -p data logs config_data

echo ""
echo "======================================"
echo "Starting Flask application..."
echo "======================================"
echo ""
echo "Open your browser and navigate to:"
echo "http://localhost:5151"
echo ""
echo "Press Ctrl+C to stop the server"
echo ""

# Run the application
python app.py

