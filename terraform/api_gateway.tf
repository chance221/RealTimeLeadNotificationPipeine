# ============================================================================
# Step 2b: API Gateway -> SQS Direct Integration (Zero-Compute Webhook Receiver)
# ============================================================================

# IAM Role allowing API Gateway to send messages directly to the SQS Processing Queue
resource "aws_iam_role" "apigw_sqs_role" {
  name = "${var.project_name}-${var.environment}-apigw-sqs-role"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Action = "sts:AssumeRole"
        Effect = "Allow"
        Principal = {
          Service = "apigateway.amazonaws.com"
        }
      }
    ]
  })
}

resource "aws_iam_role_policy" "apigw_sqs_policy" {
  name = "${var.project_name}-${var.environment}-apigw-sqs-policy"
  role = aws_iam_role.apigw_sqs_role.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect   = "Allow"
        Action   = ["sqs:SendMessage"]
        Resource = aws_sqs_queue.processing_queue.arn
      }
    ]
  })
}

# Current AWS account ID (used to construct the SQS integration URI)
data "aws_caller_identity" "current" {}

# REST API Gateway
resource "aws_api_gateway_rest_api" "webhook_api" {
  name        = "${var.project_name}-${var.environment}-webhook-api"
  description = "Receives Close CRM lead webhooks and forwards directly to Amazon SQS"

  endpoint_configuration {
    types = ["REGIONAL"]
  }
}

# /webhooks resource
resource "aws_api_gateway_resource" "webhooks" {
  rest_api_id = aws_api_gateway_rest_api.webhook_api.id
  parent_id   = aws_api_gateway_rest_api.webhook_api.root_resource_id
  path_part   = "webhooks"
}

# /webhooks/close resource
resource "aws_api_gateway_resource" "close" {
  rest_api_id = aws_api_gateway_rest_api.webhook_api.id
  parent_id   = aws_api_gateway_resource.webhooks.id
  path_part   = "close"
}

# POST /webhooks/close method
resource "aws_api_gateway_method" "post_close_webhook" {
  rest_api_id   = aws_api_gateway_rest_api.webhook_api.id
  resource_id   = aws_api_gateway_resource.close.id
  http_method   = "POST"
  authorization = "NONE"
}

# Direct AWS Service Integration from API Gateway to SQS
# Uses a Velocity Mapping Template (VTL) to URL-encode the raw JSON webhook body
# into an SQS SendMessage action without invoking a Lambda function.
resource "aws_api_gateway_integration" "sqs_integration" {
  rest_api_id             = aws_api_gateway_rest_api.webhook_api.id
  resource_id             = aws_api_gateway_resource.close.id
  http_method             = aws_api_gateway_method.post_close_webhook.http_method
  type                    = "AWS"
  integration_http_method = "POST"
  credentials             = aws_iam_role.apigw_sqs_role.arn
  uri                     = "arn:aws:apigateway:${var.aws_region}:sqs:path/${data.aws_caller_identity.current.account_id}/${aws_sqs_queue.processing_queue.name}"
  passthrough_behavior    = "NEVER"

  request_parameters = {
    "integration.request.header.Content-Type" = "'application/x-www-form-urlencoded'"
  }

  request_templates = {
    "application/json" = "Action=SendMessage&MessageBody=$util.urlEncode($input.body)"
  }
}

# 200 OK Method Response returned to Close CRM
resource "aws_api_gateway_method_response" "response_200" {
  rest_api_id = aws_api_gateway_rest_api.webhook_api.id
  resource_id = aws_api_gateway_resource.close.id
  http_method = aws_api_gateway_method.post_close_webhook.http_method
  status_code = "200"

  response_models = {
    "application/json" = "Empty"
  }
}

# Map SQS 200 response back to API Gateway 200 response
resource "aws_api_gateway_integration_response" "sqs_integration_response" {
  rest_api_id = aws_api_gateway_rest_api.webhook_api.id
  resource_id = aws_api_gateway_resource.close.id
  http_method = aws_api_gateway_method.post_close_webhook.http_method
  status_code = aws_api_gateway_method_response.response_200.status_code

  response_templates = {
    "application/json" = jsonencode({
      status  = "queued"
      message = "Webhook payload accepted into SQS processing queue"
    })
  }

  depends_on = [aws_api_gateway_integration.sqs_integration]
}

# API Gateway Deployment & Stage
resource "aws_api_gateway_deployment" "webhook_deployment" {
  rest_api_id = aws_api_gateway_rest_api.webhook_api.id

  triggers = {
    redeployment = sha1(jsonencode([
      aws_api_gateway_resource.webhooks.id,
      aws_api_gateway_resource.close.id,
      aws_api_gateway_method.post_close_webhook.id,
      aws_api_gateway_integration.sqs_integration.id,
      aws_api_gateway_integration_response.sqs_integration_response.id,
    ]))
  }

  lifecycle {
    create_before_destroy = true
  }
}

resource "aws_api_gateway_stage" "webhook_stage" {
  deployment_id = aws_api_gateway_deployment.webhook_deployment.id
  rest_api_id   = aws_api_gateway_rest_api.webhook_api.id
  stage_name    = var.environment
}
