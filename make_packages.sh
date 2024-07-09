#!/usr/bin/env bash

set -eu

# install python packages
# ref https://zenn.dev/galapagos/articles/a222e38a32f4ba

pip install -r requirements.txt -t ./package --upgrade 

# make deployment package
# original from https://docs.aws.amazon.com/ja_jp/lambda/latest/dg/python-package.html

rm my_deployment_package.zip

cd package
zip -r ../my_deployment_package.zip .

cd ..
zip my_deployment_package.zip *.py

echo "finished."
