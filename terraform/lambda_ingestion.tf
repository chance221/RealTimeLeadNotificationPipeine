# ============================================================================
# Step 3: Ingestion Lambda Function (Polls SQS -> Writes JSON to S3 in-process/)
# ============================================================================

# CloudWatch Log Group with explicit retention (avoids infinite log storage costs)
resource "aws_cloudwatch_log_group" "ingestion_lambda" {
  name              = "/aws/lambda/${var.project_name}-${var.environment}-ingestion"
  retention_in_days = 14
}

# IAM Role for the Ingestion Lambda
resource "aws_iam_role" "ingestion_lambda_role" {
  name = "${var.project_name}-${var.environment}-ingestion-lambda-role"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Action = "sts:AssumeRole"
        Effect = "Allow"
        Principal = {
          Service = "lambda.amazonaws.com"
        }
      }
    ]
  })
}

# Least-Privilege IAM Policy for the Ingestion Lambda:
# 1. Poll messages from the SQS Processing Queue
# 2. Write raw lead JSON files into the Data Lake S3 bucket under `in-process/`
# 3. Send failed invocation payloads to the central Dead-Letter Queue (DLQ)
# 4. Write execution logs to CloudWatch Logs
resource "aws_iam_role_policy" "ingestion_lambda_policy" {
  name = "${var.project_name}-${var.environment}-ingestion-lambda-policy"
  role = aws_iam_role.ingestion_lambda_role.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "ConsumeFromSQSProcessingQueue"
        Effect = "Allow"
        Action = [
          "sqs:ReceiveMessage",
          "sqs:DeleteMessage",
          "sqs:GetQueueAttributes",
          "sqs:ChangeMessageVisibility"
        ]
        Resource = aws_sqs_queue.processing_queue.arn
      },
      {
        Sid    = "WriteRawLeadsToS3InProcessFolder"
        Effect = "Allow"
        Action = [
          "s3:PutObject",
          "s3:PutObjectTagging"
        ]
        Resource = "${aws_s3_bucket.data_lake.arn}/in-process/*"
      },
      {
        Sid      = "SendFailureNotificationsToDLQ"
        Effect   = "Allow"
        Action   = ["sqs:SendMessage"]
        Resource = aws_sqs_queue.dlq.arn
      },
      {
        Sid    = "WriteCloudWatchLogs"
        Effect = "Allow"
        Action = [
          "logs:CreateLogStream",
          "logs:PutLogEvents"
        ]
        Resource = "${aws_cloudwatch_log_group.ingestion_lambda.arn}:*"
      }
    ]
  })
}

# Ingestion Lambda Function
resource "aws_lambda_function" "ingestion" {
  function_name = "${var.project_name}-${var.environment}-ingestion"
  description   = "Polls SQS processing queue and writes raw Close webhook JSON payloads to S3 in-process/ folder"
  role          = aws_iam_role.ingestion_lambda_role.arn

  s3_bucket         = aws_s3_bucket.artifacts.id
  s3_key            = aws_s3_object.ingestion_lambda_zip.key
  s3_object_version = aws_s3_object.ingestion_lambda_zip.version_id
  source_code_hash  = data.archive_file.ingestion_zip.output_base64sha256

  runtime     = "python3.12"
  handler     = "handler.lambda_handler"
  timeout     = 30
  memory_size = 256

  dead_letter_config {
    target_arn = aws_sqs_queue.dlq.arn
  }

  environment {
    variables = {
      DATA_LAKE_BUCKET_NAME = aws_s3_bucket.data_lake.id
      IN_PROCESS_PREFIX     = "in-process"
    }
  }

  depends_on = [
    aws_cloudwatch_log_group.ingestion_lambda,
    aws_iam_role_policy.ingestion_lambda_policy
  ]
}

# SQS Event Source Mapping (connects SQS Processing Queue -> Ingestion Lambda)
# Enabled with ReportBatchItemFailures so partial batch failures only retry the failed messages
# before SQS redrives them to the central Dead-Letter Queue (after maxReceiveCount = 3).
resource "aws_lambda_event_source_mapping" "sqs_to_ingestion_lambda" {
  event_source_arn                   = aws_sqs_queue.processing_queue.arn
  function_name                      = aws_lambda_function.ingestion.arn
  batch_size                         = 10
  maximum_batching_window_in_seconds = 0 # Process immediately for real-time responsiveness
  function_response_types            = ["ReportBatchItemFailures"]
  enabled                            = true
}
