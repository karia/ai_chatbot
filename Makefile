.PHONY: build init-infra deploy-infra deploy-app destroy

ENV ?= dev
export TF_DATA_DIR := $(CURDIR)/terraform/.terraform-$(ENV)

build:
	rm -rf .build
	mkdir -p .build/ingress .build/worker
	UV_CACHE_DIR=$(CURDIR)/.cache/uv uv pip install --python-version 3.14 --python-platform aarch64-manylinux2014 --only-binary :all: --target .build/worker -r src/worker/requirements.txt
	cp src/ingress/*.py .build/ingress/
	cp src/worker/*.py .build/worker/

init-infra:
	test -f terraform/$(ENV).tfvars
	test -f terraform/$(ENV).tfbackend
	terraform -chdir=terraform init -reconfigure -backend-config=$(ENV).tfbackend

deploy-infra: init-infra
	terraform -chdir=terraform apply -var-file=$(ENV).tfvars

deploy-app: init-infra build
	APP_CONFIG="$$(terraform -chdir=terraform output -json app_config)" lambroll deploy --function src/ingress/function.jsonnet --src .build/ingress --no-publish
	APP_CONFIG="$$(terraform -chdir=terraform output -json app_config)" lambroll deploy --function src/worker/function.jsonnet --src .build/worker --no-publish

destroy: init-infra
	terraform -chdir=terraform destroy -var-file=$(ENV).tfvars
