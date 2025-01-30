#!/usr/bin/env bash

set -eu

# install python packages
# ref https://zenn.dev/galapagos/articles/a222e38a32f4ba

pip install -r requirements.txt -t ./package --upgrade 

# deploy
# https://github.com/fujiwara/lambroll

cp *.py package
lambroll deploy --src="package"
rm -r package

echo "finished."
