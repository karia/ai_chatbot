.PHONY: build deploy-infra deploy-app destroy

build:
	rm -rf .build
	UV_CACHE_DIR=$(CURDIR)/.cache/uv uv pip install --python-version 3.12 --python-platform aarch64-manylinux2014 --only-binary :all: --target .build -r app/requirements.txt
	cp app/handler.py .build/handler.py

deploy-infra:
	terraform -chdir=terraform init $(if $(wildcard terraform/backend.tfbackend),-backend-config=backend.tfbackend)
	terraform -chdir=terraform apply

deploy-app: build
	cd app && \
	  LAMBDA_FUNCTION_NAME="$$(terraform -chdir=../terraform output -raw lambda_function_name)" \
	  LAMBDA_ROLE_ARN="$$(terraform -chdir=../terraform output -raw lambda_role_arn)" \
	  MEMORY_ID="$$(terraform -chdir=../terraform output -raw memory_id)" \
	  AWS_REGION="$$(terraform -chdir=../terraform output -raw aws_region)" \
	  lambroll deploy --function function.jsonnet --src ../.build --no-publish

destroy:
	terraform -chdir=terraform destroy
