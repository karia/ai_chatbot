mock_provider "aws" {
  override_during = plan
  mock_resource "aws_iam_policy" {
    defaults = { arn = format("arn:aws:iam::%012d:policy/test-boundary", 0) }
  }
  mock_resource "aws_iam_role" {
    defaults = { arn = format("arn:aws:iam::%012d:role/test-deploy", 0) }
  }
  mock_resource "aws_apigatewayv2_api" {
    defaults = {
      id            = "mock-api"
      execution_arn = format("arn:aws:execute-api:ap-northeast-1:%012d:mock-api", 0)
    }
  }
  mock_resource "aws_lambda_alias" {
    defaults = { arn = format("arn:aws:lambda:ap-northeast-1:%012d:function:test:current", 0), invoke_arn = format("arn:aws:lambda:ap-northeast-1:%012d:function:test:current", 0) }
  }
  mock_resource "aws_lambda_event_source_mapping" {
    defaults = { arn = format("arn:aws:lambda:ap-northeast-1:%012d:event-source-mapping:test", 0) }
  }
}
mock_provider "archive" {
  override_during = plan
}

variables {
  environment  = "dev"
  log_level    = "DEBUG"
  state_bucket = "test-state"
}

run "deployment_contract" {
  command = plan

  assert {
    condition = jsondecode(aws_iam_role.deploy.assume_role_policy).Statement[0].Condition.StringEquals["token.actions.githubusercontent.com:sub"] == [
      "repo:karia/ai_chatbot:environment:dev:ref:refs/heads/main:event_name:push",
      "repo:karia/ai_chatbot:environment:dev:ref:refs/heads/main:event_name:workflow_dispatch"
    ]
    error_message = "OIDC must restrict repository, environment, branch and event."
  }

  assert {
    condition = (
      length(aws_iam_openid_connect_provider.github) == 1 &&
      aws_lambda_function.app["ingress"].publish &&
      aws_lambda_alias.current["worker"].name == "current" &&
      aws_lambda_permission.ingress.qualifier == "current" &&
      aws_apigatewayv2_integration.ingress.integration_uri == aws_lambda_alias.current["ingress"].invoke_arn &&
      aws_lambda_event_source_mapping.worker.function_name == aws_lambda_alias.current["worker"].arn &&
      alltrue([for role in aws_iam_role.app : role.permissions_boundary == aws_iam_policy.runtime_boundary.arn])
    )
    error_message = "Create one provider and route invocations through published aliases."
  }

  assert {
    condition = alltrue(flatten([
      for statement in jsondecode(aws_iam_role_policy.deploy.policy).Statement : [
        for action in statement.Action : !strcontains(action, "*") &&
        !contains(["iam:DeleteRolePermissionsBoundary", "iam:AttachRolePolicy", "iam:CreatePolicyVersion", "secretsmanager:GetSecretValue"], action)
      ]
    ]))
    error_message = "Deployment cannot remove boundaries, expand managed policies or read secrets."
  }

  assert {
    condition = alltrue([
      for statement in jsondecode(aws_iam_role_policy.deploy.policy).Statement :
      !contains(try(tolist(statement.Resource), [statement.Resource]), aws_iam_role.deploy.arn) ||
      alltrue([for action in statement.Action : startswith(action, "iam:Get") || startswith(action, "iam:List")])
    ])
    error_message = "The deploy role must only read its own IAM configuration."
  }

  assert {
    condition = (
      length(aws_iam_role_policy.deploy.policy) < 10240 &&
      length(aws_iam_policy.runtime_boundary.policy) < 6144
    )
    error_message = "Policies must fit IAM size limits."
  }

}

run "production_provider_is_shared" {
  command = plan
  variables {
    environment = "prod"
  }
  assert {
    condition     = length(aws_iam_openid_connect_provider.github) == 0
    error_message = "Only dev state owns the account-wide provider."
  }
}
