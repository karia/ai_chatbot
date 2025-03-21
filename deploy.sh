#!/usr/bin/env bash

# 引数チェック
if [ "$#" -lt 1 ]; then
	echo "Usage: $0 <function_file>" >&2
	exit 1
fi

FUNCTION_FILE=$1

set -eu

# Change directory
cd "$(dirname "$0")"

# Create package directory if not exists
mkdir -p package

# Clean up existing files in package directory
rm -rf package/*

# install python packages
# ref https://zenn.dev/galapagos/articles/a222e38a32f4ba

pip install -r "requirements.txt" -t "./package" --upgrade 

# deploy
# https://github.com/fujiwara/lambroll

cp -r ./src/*.py package
lambroll deploy --src="package" --function="$FUNCTION_FILE"
rm -rf package

echo "finished."
