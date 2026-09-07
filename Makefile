.PHONY: build init-infra deploy-infra deploy-app rollback-app destroy

ENV ?= dev
export LAMBROLL_LOGFORMAT := json
export TF_DATA_DIR := $(CURDIR)/terraform/.terraform-$(ENV)

build:
	rm -rf .build
	mkdir -p .build/ingress .build/worker
	UV_CACHE_DIR=$(CURDIR)/.cache/uv uv pip install --python-version 3.14 --python-platform aarch64-manylinux2014 --only-binary :all: --target .build/worker -r src/worker/requirements.txt
	cp src/ingress/*.py .build/ingress/
	cp src/worker/*.py .build/worker/

init-infra:
	test -f terraform/$(ENV).tfvars || { echo "Missing file: terraform/$(ENV).tfvars" >&2; exit 1; }
	test -f terraform/$(ENV).tfbackend || { echo "Missing file: terraform/$(ENV).tfbackend" >&2; exit 1; }
	terraform -chdir=terraform init -reconfigure -backend-config=$(ENV).tfbackend

deploy-infra: init-infra
	terraform -chdir=terraform apply -var-file=$(ENV).tfvars

deploy-app: init-infra build
	@set -e; \
	APP_CONFIG="$$(terraform -chdir=terraform output -json app_config)"; \
	export APP_CONFIG; \
	lambroll deploy --function src/ingress/function.jsonnet --src .build/ingress --alias current; \
	lambroll deploy --function src/worker/function.jsonnet --src .build/worker --alias current

destroy: init-infra
	terraform -chdir=terraform destroy -var-file=$(ENV).tfvars

rollback-app:
	@case "$(FUNCTION)" in ingress|worker) ;; *) echo "FUNCTION must be ingress or worker"; exit 1;; esac
	@case "$(VERSION)" in ''|*[!0-9]*|0) echo "VERSION must be a positive published version"; exit 1;; esac
	$(MAKE) init-infra ENV=$(ENV)
	@set -e; \
	APP_CONFIG="$$(terraform -chdir=terraform output -json app_config)"; \
	export APP_CONFIG; \
	lambroll rollback --function src/$(FUNCTION)/function.jsonnet --alias current --version $(VERSION)
