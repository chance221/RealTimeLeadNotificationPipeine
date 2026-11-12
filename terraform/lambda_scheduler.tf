# ============================================================================
# Step 4: S3 Event Notification -> Scheduler Lambda -> EventBridge Scheduler
# ============================================================================

variable "notification_delay_minutes" {
  description = "Number of minutes EventBridge Scheduler waits after a lead JSON lands in S3 before invoking the Processing Lambda"
  type        = number
  default     = 30
}

# Dedicated EventBridge Schedule Group for clean organization & teardown
resource "aws_scheduler_schedule_group" "lead_timers" {
  name = "${var.project_name}-${var.environment}-lead-timers"
}

# ----------------------------------------------------------------------------
# 4A. IAM Role assumed by EventBridge Scheduler when the 10-minute timer fires
# Allows EventBridge Scheduler to:
# 1. Invoke the Processing & SES Lambda function
# 2. Send failed target invocations to the central Dead-Letter Queue (DLQ)
# ----------------------------------------------------------------------------
resource "aws_iam_role" "eventbridge_scheduler_execution_role" {
  name = "${var.project_name}-${var.environment}-eb-scheduler-role"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Action = "sts:AssumeRole"
        Effect = "Allow"
        Principal = {
          Service = "scheduler.amazonaws.com"
        }
      }
    ]
  })
}

resource "aws_iam_role_policy" "eventbridge_scheduler_execution_policy" {
  name = "${var.project_name}-${var.environment}-eb-scheduler-policy"
  role = aws_iam_role.eventbridge_scheduler_execution_role.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "InvokeProcessingLambdaAfter10MinTimer"
        Effect = "Allow"
        Action = ["lambda:InvokeFunction"]
        Resource = [
          aws_lambda_function.processing.arn,
          "${aws_lambda_function.processing.arn}:*"
        ]
      },
      {
        Sid      = "SendFailedSchedulesToDLQ"
        Effect   = "Allow"
        Action   = ["sqs:SendMessage"]
        Resource = aws_sqs_queue.dlq.arn
      }
    ]
  })
}

# ----------------------------------------------------------------------------
# 4B. Scheduler Lambda Function (Triggered by S3 `in-process/*.json` creation)
# ----------------------------------------------------------------------------
resource "aws_cloudwatch_log_group" "scheduler_lambda" {
  name              = "/aws/lambda/${var.project_name}-${var.environment}-scheduler"
  retention_in_days = 14
}

resource "aws_iam_role" "scheduler_lambda_role" {
  name = "${var.project_name}-${var.environment}-scheduler-lambda-role"

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

# Least-Privilege IAM Policy for the Scheduler Lambda:
# 1. Create/Get/Update schedules in the EventBridge Scheduler group
# 2. Pass the `eventbridge_scheduler_execution_role` to EventBridge Scheduler (`iam:PassRole`)
# 3. Send failed async Lambda invocations to the central Dead-Letter Queue (DLQ)
# 4. Write execution logs to CloudWatch Logs
resource "aws_iam_role_policy" "scheduler_lambda_policy" {
  name = "${var.project_name}-${var.environment}-scheduler-lambda-policy"
  role = aws_iam_role.scheduler_lambda_role.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "CreateOneTimeEventBridgeSchedules"
        Effect = "Allow"
        Action = [
          "scheduler:CreateSchedule",
          "scheduler:UpdateSchedule",
          "scheduler:GetSchedule",
          "scheduler:DeleteSchedule"
        ]
        Resource = "arn:aws:scheduler:${var.aws_region}:${data.aws_caller_identity.current.account_id}:schedule/${aws_scheduler_schedule_group.lead_timers.name}/lead-*"
      },
      {
        Sid      = "PassExecutionRoleToEventBridgeScheduler"
        Effect   = "Allow"
        Action   = ["iam:PassRole"]
        Resource = aws_iam_role.eventbridge_scheduler_execution_role.arn
        Condition = {
          StringEquals = {
            "iam:PassedToService" = "scheduler.amazonaws.com"
          }
        }
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
        Resource = "${aws_cloudwatch_log_group.scheduler_lambda.arn}:*"
      }
    ]
  })
}

resource "aws_lambda_function" "scheduler" {
  function_name = "${var.project_name}-${var.environment}-scheduler"
  description   = "Triggered by S3 in-process/ object creation; creates a 10-minute one-time timer in EventBridge Scheduler"
  role          = aws_iam_role.scheduler_lambda_role.arn

  s3_bucket         = aws_s3_bucket.artifacts.id
  s3_key            = aws_s3_object.scheduler_lambda_zip.key
  s3_object_version = aws_s3_object.scheduler_lambda_zip.version_id
  source_code_hash  = data.archive_file.scheduler_zip.output_base64sha256

  runtime     = "python3.12"
  handler     = "handler.lambda_handler"
  timeout     = 30
  memory_size = 256

  dead_letter_config {
    target_arn = aws_sqs_queue.dlq.arn
  }

  environment {
    variables = {
      PROCESSING_LAMBDA_ARN = aws_lambda_function.processing.arn
      SCHEDULER_ROLE_ARN    = aws_iam_role.eventbridge_scheduler_execution_role.arn
      DLQ_ARN               = aws_sqs_queue.dlq.arn
      SCHEDULE_GROUP_NAME   = aws_scheduler_schedule_group.lead_timers.name
      DELAY_MINUTES         = tostring(var.notification_delay_minutes)
    }
  }

  depends_on = [
    aws_cloudwatch_log_group.scheduler_lambda,
    aws_iam_role_policy.scheduler_lambda_policy
  ]
}

# Configure async invocation retry & destination to DLQ for the Scheduler Lambda
resource "aws_lambda_function_event_invoke_config" "scheduler_async_dlq" {
  function_name          = aws_lambda_function.scheduler.function_name
  maximum_retry_attempts = 2

  destination_config {
    on_failure {
      destination = aws_sqs_queue.dlq.arn
    }
  }
}

# ----------------------------------------------------------------------------
# 4C. S3 Bucket Event Notification (`s3://real-time-close-api-messages/in-process/*.json`)
# ----------------------------------------------------------------------------
resource "aws_lambda_permission" "allow_s3_to_invoke_scheduler" {
  statement_id   = "AllowExecutionFromS3DataLakeBucket"
  action         = "lambda:InvokeFunction"
  function_name  = aws_lambda_function.scheduler.function_name
  principal      = "s3.amazonaws.com"
  source_arn     = aws_s3_bucket.data_lake.arn
  source_account = data.aws_caller_identity.current.account_id
}

resource "aws_s3_bucket_notification" "in_process_lead_created" {
  bucket = aws_s3_bucket.data_lake.id

  lambda_function {
    lambda_function_arn = aws_lambda_function.scheduler.arn
    events              = ["s3:ObjectCreated:*"]
    filter_prefix       = "in-process/"
    filter_suffix       = ".json"
  }

  depends_on = [aws_lambda_permission.allow_s3_to_invoke_scheduler]
}
