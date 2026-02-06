#!/bin/bash
# Build the Anki addon package

cd "$(dirname "$0")"

# Create the .ankiaddon file (which is just a zip)
zip -j lain_sync.ankiaddon __init__.py manifest.json

echo "Created lain_sync.ankiaddon"
echo "Users can double-click this file to install the plugin."
