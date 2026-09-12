data "aws_caller_identity" "current" {}
data "aws_partition" "current" {}
data "aws_region" "current" {}

variable "state_bucket" {
  type        = string
  description = "Existing S3 backend bucket, supplied privately via TF_VAR_state_bucket."
  validation {
    condition     = length(var.state_bucket) > 0
    error_message = "Supply the existing backend bucket name."
  }
}

locals {
  account_arn   = "arn:${data.aws_partition.current.partition}"
  regional_arn  = "${local.account_arn}:%s:${data.aws_region.current.region}:${data.aws_caller_identity.current.account_id}"
  oidc_arn      = "${local.account_arn}:iam::${data.aws_caller_identity.current.account_id}:oidc-provider/token.actions.githubusercontent.com"
  app_role_arns = [for name in local.function_names : "${local.account_arn}:iam::${data.aws_caller_identity.current.account_id}:role/${local.prefix}-${name}"]
  function_arns = flatten([for name in local.function_names : [
    "${format(local.regional_arn, "lambda")}:function:${local.prefix}-${name}",
    "${format(local.regional_arn, "lambda")}:function:${local.prefix}-${name}:*"
  ]])
  state_key = "ai-chatbot/${var.environment}/terraform.tfstate"
}

resource "aws_iam_openid_connect_provider" "github" {
  count          = var.environment == "dev" ? 1 : 0
  url            = "https://token.actions.githubusercontent.com"
  client_id_list = ["sts.amazonaws.com"]
  lifecycle {
    prevent_destroy = true
  }
}

resource "aws_iam_role" "deploy" {
  name = "${local.prefix}-deploy"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Federated = local.oidc_arn }
      Action    = "sts:AssumeRoleWithWebIdentity"
      Condition = { StringEquals = {
        "token.actions.githubusercontent.com:aud" = "sts.amazonaws.com"
        "token.actions.githubusercontent.com:sub" = [for event in ["push", "workflow_dispatch"] :
          "repo:karia/ai_chatbot:environment:${var.environment}:ref:refs/heads/main:event_name:${event}"
        ]
      } }
    }]
  })
  depends_on = [aws_iam_openid_connect_provider.github]
}

resource "aws_iam_policy" "runtime_boundary" {
  name = "${local.prefix}-runtime-boundary"
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = concat([
      {
        Effect   = "Allow"
        Action   = ["logs:CreateLogStream", "logs:PutLogEvents"]
        Resource = "${format(local.regional_arn, "logs")}:log-group:/aws/lambda/${local.prefix}-*:log-stream:*"
      },
      {
        Effect   = "Allow"
        Action   = ["secretsmanager:GetSecretValue"]
        Resource = "${format(local.regional_arn, "secretsmanager")}:secret:${local.prefix}/slack-*"
      },
      {
        Effect   = "Allow"
        Action   = ["sqs:SendMessage", "sqs:ReceiveMessage", "sqs:DeleteMessage", "sqs:GetQueueAttributes"]
        Resource = "${format(local.regional_arn, "sqs")}:${local.prefix}-events.fifo"
      },
      {
        Effect   = "Allow"
        Action   = ["dynamodb:GetItem", "dynamodb:PutItem", "dynamodb:UpdateItem", "dynamodb:DeleteItem"]
        Resource = "${format(local.regional_arn, "dynamodb")}:table/${local.prefix}-state"
      },
      {
        Effect   = "Allow"
        Action   = ["bedrock-agentcore:CreateEvent", "bedrock-agentcore:GetEvent", "bedrock-agentcore:ListEvents"]
        Resource = "${format(local.regional_arn, "bedrock-agentcore")}:memory/${replace(local.prefix, "-", "_")}_conversation-*"
      }
      ], length(var.bedrock_resource_arns) > 0 ? [{
        Effect   = "Allow"
        Action   = ["bedrock:InvokeModel", "bedrock:InvokeModelWithResponseStream"]
        Resource = sort(tolist(var.bedrock_resource_arns))
    }] : [])
  })
}

resource "aws_iam_role_policy" "deploy" {
  name = "${local.prefix}-deploy"
  role = aws_iam_role.deploy.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect    = "Allow"
        Action    = ["s3:ListBucket"]
        Resource  = "${local.account_arn}:s3:::${var.state_bucket}"
        Condition = { StringLike = { "s3:prefix" = [local.state_key, "${local.state_key}.tflock"] } }
      },
      {
        Effect   = "Allow"
        Action   = ["s3:GetObject", "s3:PutObject"]
        Resource = [for key in [local.state_key, "${local.state_key}.tflock"] : "${local.account_arn}:s3:::${var.state_bucket}/${key}"]
      },
      {
        Effect   = "Allow"
        Action   = ["s3:DeleteObject"]
        Resource = "${local.account_arn}:s3:::${var.state_bucket}/${local.state_key}.tflock"
      },
      {
        Effect   = "Allow"
        Action   = ["iam:GetOpenIDConnectProvider"]
        Resource = local.oidc_arn
      },
      {
        Effect   = "Allow"
        Action   = ["iam:GetRole", "iam:GetRolePolicy", "iam:ListRolePolicies", "iam:ListAttachedRolePolicies"]
        Resource = concat(local.app_role_arns, [aws_iam_role.deploy.arn])
      },
      {
        Effect   = "Allow"
        Action   = ["iam:GetPolicy", "iam:GetPolicyVersion", "iam:ListPolicyVersions", "iam:ListEntitiesForPolicy"]
        Resource = aws_iam_policy.runtime_boundary.arn
      },
      {
        Effect    = "Allow"
        Action    = ["iam:CreateRole", "iam:PutRolePermissionsBoundary"]
        Resource  = local.app_role_arns
        Condition = { StringEquals = { "iam:PermissionsBoundary" = aws_iam_policy.runtime_boundary.arn } }
      },
      {
        Effect   = "Allow"
        Action   = ["iam:DeleteRole", "iam:UpdateRole", "iam:UpdateRoleDescription", "iam:UpdateAssumeRolePolicy", "iam:PutRolePolicy", "iam:DeleteRolePolicy", "iam:TagRole", "iam:UntagRole"]
        Resource = local.app_role_arns
      },
      {
        Effect    = "Allow"
        Action    = ["iam:PassRole"]
        Resource  = local.app_role_arns
        Condition = { StringEquals = { "iam:PassedToService" = "lambda.amazonaws.com" } }
      },
      {
        Effect   = "Allow"
        Action   = ["lambda:CreateFunction", "lambda:GetFunction", "lambda:GetFunctionConfiguration", "lambda:GetFunctionCodeSigningConfig", "lambda:GetRuntimeManagementConfig", "lambda:GetFunctionRecursionConfig", "lambda:UpdateFunctionCode", "lambda:UpdateFunctionConfiguration", "lambda:DeleteFunction", "lambda:PublishVersion", "lambda:GetAlias", "lambda:CreateAlias", "lambda:UpdateAlias", "lambda:DeleteAlias", "lambda:ListAliases", "lambda:ListVersionsByFunction", "lambda:GetFunctionConcurrency", "lambda:PutFunctionConcurrency", "lambda:DeleteFunctionConcurrency", "lambda:GetPolicy", "lambda:AddPermission", "lambda:RemovePermission", "lambda:ListTags", "lambda:TagResource", "lambda:UntagResource"]
        Resource = local.function_arns
      },
      {
        Effect    = "Allow"
        Action    = ["lambda:CreateEventSourceMapping", "lambda:UpdateEventSourceMapping", "lambda:DeleteEventSourceMapping", "lambda:GetEventSourceMapping"]
        Resource  = "*"
        Condition = { ArnEquals = { "lambda:FunctionArn" = ["${format(local.regional_arn, "lambda")}:function:${local.prefix}-worker", "${format(local.regional_arn, "lambda")}:function:${local.prefix}-worker:current"] } }
      },
      {
        Effect   = "Allow"
        Action   = ["lambda:ListTags", "lambda:TagResource", "lambda:UntagResource"]
        Resource = aws_lambda_event_source_mapping.worker.arn
      },
      {
        Effect = "Allow"
        Action = ["logs:CreateLogGroup", "logs:DeleteLogGroup", "logs:PutRetentionPolicy", "logs:DeleteRetentionPolicy", "logs:ListTagsForResource", "logs:TagResource", "logs:UntagResource"]
        Resource = flatten([for path in ["lambda/${local.prefix}-*", "apigateway/${local.prefix}-*"] : [
          "${format(local.regional_arn, "logs")}:log-group:/aws/${path}",
          "${format(local.regional_arn, "logs")}:log-group:/aws/${path}:*"
        ]])
      },
      {
        Effect    = "Allow"
        Action    = ["logs:DescribeLogGroups", "logs:CreateLogDelivery", "logs:UpdateLogDelivery", "logs:DeleteLogDelivery", "logs:GetLogDelivery", "logs:ListLogDeliveries", "logs:PutResourcePolicy", "logs:DescribeResourcePolicies"]
        Resource  = "*"
        Condition = { StringEquals = { "aws:RequestedRegion" = data.aws_region.current.region } }
      },
      {
        Effect   = "Allow"
        Action   = ["sqs:CreateQueue", "sqs:DeleteQueue", "sqs:GetQueueAttributes", "sqs:GetQueueUrl", "sqs:SetQueueAttributes", "sqs:ListQueueTags", "sqs:TagQueue", "sqs:UntagQueue"]
        Resource = "${format(local.regional_arn, "sqs")}:${local.prefix}-events*.fifo"
      },
      {
        Effect   = "Allow"
        Action   = ["dynamodb:CreateTable", "dynamodb:DeleteTable", "dynamodb:DescribeTable", "dynamodb:UpdateTable", "dynamodb:DescribeContinuousBackups", "dynamodb:UpdateContinuousBackups", "dynamodb:DescribeTimeToLive", "dynamodb:UpdateTimeToLive", "dynamodb:ListTagsOfResource", "dynamodb:TagResource", "dynamodb:UntagResource", "dynamodb:DescribeContributorInsights"]
        Resource = "${format(local.regional_arn, "dynamodb")}:table/${local.prefix}-state"
      },
      {
        Effect   = "Allow"
        Action   = ["secretsmanager:DescribeSecret", "secretsmanager:GetResourcePolicy"]
        Resource = "${format(local.regional_arn, "secretsmanager")}:secret:${local.prefix}/slack-*"
      },
      {
        Effect    = "Allow"
        Action    = ["bedrock-agentcore:CreateMemory"]
        Resource  = "*"
        Condition = { StringEquals = { "aws:RequestedRegion" = data.aws_region.current.region, "aws:RequestTag/ProjectEnvironment" = local.prefix } }
      },
      {
        Effect   = "Allow"
        Action   = ["bedrock-agentcore:GetMemory", "bedrock-agentcore:UpdateMemory", "bedrock-agentcore:DeleteMemory", "bedrock-agentcore:ListTagsForResource", "bedrock-agentcore:TagResource", "bedrock-agentcore:UntagResource"]
        Resource = "${format(local.regional_arn, "bedrock-agentcore")}:memory/${replace(local.prefix, "-", "_")}_conversation-*"
      },
      {
        Effect   = "Allow"
        Action   = ["apigateway:GET", "apigateway:POST", "apigateway:PUT", "apigateway:PATCH", "apigateway:DELETE"]
        Resource = [for path in ["/apis/${aws_apigatewayv2_api.slack.id}", "/apis/${aws_apigatewayv2_api.slack.id}/*", "/tags/${urlencode("${local.account_arn}:apigateway:${data.aws_region.current.region}::/apis/${aws_apigatewayv2_api.slack.id}")}"] : "${local.account_arn}:apigateway:${data.aws_region.current.region}::${path}"]
      },
      {
        Effect    = "Allow"
        Action    = ["apigateway:POST"]
        Resource  = "${local.account_arn}:apigateway:${data.aws_region.current.region}::/apis"
        Condition = { StringEquals = { "apigateway:Request/ApiName" = "${local.prefix}-slack" } }
      }
    ]
  })
}

output "deploy_role_arn" {
  value = aws_iam_role.deploy.arn
}
