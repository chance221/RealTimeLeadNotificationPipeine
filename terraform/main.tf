# ============================================================================
# Step 1: S3 Artifact Bucket & Data Lake Bucket (Fully Managed by Terraform)
# ============================================================================

# ----------------------------------------------------------------------------
# 1A. Artifact S3 Bucket (`real-time-close-api-scripts`)
# Stores packaged Python Lambda `.zip` files and raw scripts under:
#   - artifacts/lambda_functions/ingestion/
#   - artifacts/lambda_functions/scheduler/
#   - artifacts/lambda_functions/processing/
# ----------------------------------------------------------------------------
resource "aws_s3_bucket" "artifacts" {
  bucket        = var.artifact_bucket_name
  force_destroy = true
}

resource "aws_s3_bucket_versioning" "artifacts" {
  bucket = aws_s3_bucket.artifacts.id

  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "artifacts" {
  bucket = aws_s3_bucket.artifacts.id

  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

resource "aws_s3_bucket_public_access_block" "artifacts" {
  bucket = aws_s3_bucket.artifacts.id

  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

# Automatically expire noncurrent (older) object versions after 14 days (2 weeks)
# and clean up expired delete markers so storage costs never grow unbounded.
resource "aws_s3_bucket_lifecycle_configuration" "artifacts_lifecycle" {
  bucket = aws_s3_bucket.artifacts.id

  rule {
    id     = "expire-noncurrent-versions-after-14-days"
    status = "Enabled"

    filter {}

    noncurrent_version_expiration {
      noncurrent_days = 14
    }

    expiration {
      expired_object_delete_marker = true
    }

    abort_incomplete_multipart_upload {
      days_after_initiation = 7
    }
  }

  depends_on = [aws_s3_bucket_versioning.artifacts]
}

# Explicit folder markers in the Artifacts bucket for the 3 Lambda functions
# Scoped by the immutable Git commit SHA (`var.git_commit_sha`) so every run is traceable
resource "aws_s3_object" "artifacts_ingestion_prefix" {
  bucket  = aws_s3_bucket.artifacts.id
  key     = "artifacts/${var.git_commit_sha}/lambda_functions/ingestion/"
  content = ""
}

resource "aws_s3_object" "artifacts_scheduler_prefix" {
  bucket  = aws_s3_bucket.artifacts.id
  key     = "artifacts/${var.git_commit_sha}/lambda_functions/scheduler/"
  content = ""
}

resource "aws_s3_object" "artifacts_processing_prefix" {
  bucket  = aws_s3_bucket.artifacts.id
  key     = "artifacts/${var.git_commit_sha}/lambda_functions/processing/"
  content = ""
}

# Package each Lambda directory (including Linux x86_64 dependencies installed into `terraform/dist/{fn}`)
# into a .zip and upload to the SHA-versioned Artifacts S3 folder
data "archive_file" "ingestion_zip" {
  type        = "zip"
  source_dir  = "${path.module}/dist/ingestion"
  output_path = "${path.module}/build/ingestion.zip"
}

resource "aws_s3_object" "ingestion_lambda_zip" {
  bucket      = aws_s3_bucket.artifacts.id
  key         = "artifacts/${var.git_commit_sha}/lambda_functions/ingestion/ingestion.zip"
  source      = data.archive_file.ingestion_zip.output_path
  source_hash = data.archive_file.ingestion_zip.output_base64sha256
}

data "archive_file" "scheduler_zip" {
  type        = "zip"
  source_dir  = "${path.module}/dist/scheduler"
  output_path = "${path.module}/build/scheduler.zip"
}

resource "aws_s3_object" "scheduler_lambda_zip" {
  bucket      = aws_s3_bucket.artifacts.id
  key         = "artifacts/${var.git_commit_sha}/lambda_functions/scheduler/scheduler.zip"
  source      = data.archive_file.scheduler_zip.output_path
  source_hash = data.archive_file.scheduler_zip.output_base64sha256
}

data "archive_file" "processing_zip" {
  type        = "zip"
  source_dir  = "${path.module}/dist/processing"
  output_path = "${path.module}/build/processing.zip"
}

resource "aws_s3_object" "processing_lambda_zip" {
  bucket      = aws_s3_bucket.artifacts.id
  key         = "artifacts/${var.git_commit_sha}/lambda_functions/processing/processing.zip"
  source      = data.archive_file.processing_zip.output_path
  source_hash = data.archive_file.processing_zip.output_base64sha256
}

# ----------------------------------------------------------------------------
# 1B. Data Lake S3 Bucket (`real-time-close-api-messages`)
# Stores raw Close webhook leads in `in-process/` and completed leads in `processed/`
# ----------------------------------------------------------------------------
resource "aws_s3_bucket" "data_lake" {
  bucket        = var.data_lake_bucket_name
  force_destroy = true
}

resource "aws_s3_bucket_versioning" "data_lake" {
  bucket = aws_s3_bucket.data_lake.id

  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "data_lake" {
  bucket = aws_s3_bucket.data_lake.id

  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

resource "aws_s3_bucket_public_access_block" "data_lake" {
  bucket = aws_s3_bucket.data_lake.id

  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

# Automatically expire noncurrent (older) object versions after 14 days (2 weeks)
# (e.g., when an object is deleted from `in-process/` after moving to `processed/`,
# S3 versioning keeps the noncurrent version for 14 days for safety and then expires it).
resource "aws_s3_bucket_lifecycle_configuration" "data_lake_lifecycle" {
  bucket = aws_s3_bucket.data_lake.id

  rule {
    id     = "expire-noncurrent-versions-after-14-days"
    status = "Enabled"

    filter {}

    noncurrent_version_expiration {
      noncurrent_days = 14
    }

    expiration {
      expired_object_delete_marker = true
    }

    abort_incomplete_multipart_upload {
      days_after_initiation = 7
    }
  }

  depends_on = [aws_s3_bucket_versioning.data_lake]
}

# Create explicit folder markers for in-process/ and processed/ prefixes
resource "aws_s3_object" "in_process_prefix" {
  bucket  = aws_s3_bucket.data_lake.id
  key     = "in-process/"
  content = ""
}

resource "aws_s3_object" "processed_prefix" {
  bucket  = aws_s3_bucket.data_lake.id
  key     = "processed/"
  content = ""
}
