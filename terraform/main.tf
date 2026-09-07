terraform {
  required_version = ">= 1.15"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = ">= 6.18.0, < 7.0.0"
    }
    archive = {
      source  = "hashicorp/archive"
      version = "~> 2.0"
    }
  }
  backend "s3" {
    region       = "ap-northeast-1"
    encrypt      = true
    use_lockfile = true
  }
}
provider "aws" {
  region = "ap-northeast-1"
}

locals {
  function_names = ["ingress", "worker"]
  prefix         = "${var.project_name}-${var.environment}"
}

resource "aws_bedrockagentcore_memory" "conversation" {
  name                  = "${replace(local.prefix, "-", "_")}_conversation"
  event_expiry_duration = 30
  tags                  = { ProjectEnvironment = local.prefix }
}

data "aws_secretsmanager_secret" "signing" {
  name = "${local.prefix}/slack-signing-secret"
}

data "aws_secretsmanager_secret" "bot" {
  name = "${local.prefix}/slack-bot-token"
}
