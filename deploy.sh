#!/usr/bin/env bash

set -eu

# Change directory
cd "$(dirname "$0")"

# Create package directory if not exists
mkdir -p package

# Clean up existing files in package directory
rm -rf package/*

# install python packages
# ref https://zenn.dev/galapagos/articles/a222e38a32f4ba

python3 -m pip install -r "requirements.txt" -t "./package" --upgrade 

# deploy
# https://github.com/fujiwara/lambroll

cp ./*.py package
lambroll deploy --src="package"
rm -rf package

echo "finished."
