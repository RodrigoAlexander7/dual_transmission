#!/bin/bash
# Install dependencies for Linux

echo "Installing dependencies - remember to use sudo"
sudo apt update

echo "Installing v4l-utils --camera parameters control"
sudo apt install -y v4l-utils

echo "Installing fswebcam --camera capture utility"
sudo apt install fswebcam