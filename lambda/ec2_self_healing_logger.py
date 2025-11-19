import json
import os
import uuid
import datetime

import boto3

# AWS clients
ec2 = boto3.client("ec2")
s3 = boto3.client("s3")

# Environment variables (configure these in the Lambda console)
LOG_BUCKET = os.environ["LOG_BUCKET"]       # e.g. scott-ec2-self-healing-logs
INSTANCE_ID = os.environ.get("INSTANCE_ID", "unknown")  # demo EC2 instance ID


def lambda_handler(event, context):
    """
    Lambda function triggered by an SNS notification from a CloudWatch Alarm.

    Responsibilities:
      1) Parse the alarm message received via SNS
      2) Extract basic information (alarm name, state, instance ID, reason)
      3) Decide whether to attempt a self-healing action (EC2 reboot)
      4) Write a structured JSON log entry to S3 (one file per alarm event)
    """

    # 1) Get the SNS message payload (CloudWatch Alarm -> SNS -> Lambda)
    try:
        sns_record = event["Records"][0]["Sns"]
        message_str = sns_record["Message"]
    except (KeyError, IndexError) as e:
        # If the event format is unexpected, store the raw event for debugging
        log_item = {
            "timestamp": utc_now(),
            "error": f"Unexpected event format: {e}",
            "raw_event": event,
        }
        _write_log_to_s3(log_item, prefix="errors/")
        return {"statusCode": 400, "body": "Unexpected event format"}

    # 2) CloudWatch Alarm -> SNS usually sends a JSON string as the message
    try:
        alarm_message = json.loads(message_str)
    except json.JSONDecodeError:
        # If parsing fails, keep the raw string so we can inspect it later
        alarm_message = {"raw_message": message_str}

    # 3) Extract key fields from the alarm message (with safe defaults)
    alarm_name = alarm_message.get("AlarmName", "unknown")
    new_state = alarm_message.get("NewStateValue", "unknown")
    reason = alarm_message.get("NewStateReason", "")
    region = alarm_message.get("Region", os.environ.get("AWS_REGION", "unknown"))

    # Instance ID:
    # - default to the INSTANCE_ID environment variable (demo instance)
    # - override if the CloudWatch alarm Trigger includes an InstanceId dimension
    instance_id = INSTANCE_ID

    trigger = alarm_message.get("Trigger", {})
    for dim in trigger.get("Dimensions", []):
        if dim.get("Name") == "InstanceId" or dim.get("name") == "InstanceId":
            instance_id = dim.get("Value") or dim.get("value") or instance_id
            break

    # 4) Decide what self-healing action to take
    action_taken = "NONE"

    if new_state == "ALARM" and instance_id != "unknown":
        try:
            # Check the current EC2 instance state
            status_resp = ec2.describe_instance_status(
                InstanceIds=[instance_id],
                IncludeAllInstances=True,
            )

            instance_state = "unknown"
            if status_resp.get("InstanceStatuses"):
                instance_state = status_resp["InstanceStatuses"][0]["InstanceState"]["Name"]

            if instance_state == "running":
                # Self-healing action: request an EC2 reboot
                ec2.reboot_instances(InstanceIds=[instance_id])
                action_taken = f"REBOOT_REQUESTED_from_{instance_state}"
            else:
                # For other states (stopped, stopping, pending, etc.) we skip the reboot
                action_taken = f"SKIPPED_state_{instance_state}"

        except Exception as e:
            # Capture any unexpected error, but do not raise it to avoid retry storms
            action_taken = f"ERROR_{type(e).__name__}"

    # 5) Build a structured log entry
    log_item = {
        "timestamp": utc_now(),
        "alarm_name": alarm_name,
        "new_state": new_state,
        "reason": reason,
        "instance_id": instance_id,
        "region": region,
        "raw_alarm_message": alarm_message,
        "action_taken": action_taken,
    }

    # 6) Store the log item in S3 (one JSON file per alarm event)
    _write_log_to_s3(log_item)

    return {
        "statusCode": 200,
        "body": json.dumps(
            {
                "message": "alarm processed",
                "instance_id": instance_id,
                "action_taken": action_taken,
            }
        ),
    }


def _write_log_to_s3(log_item: dict, prefix: str = "logs/") -> None:
    """
    Helper: write a single JSON log entry to S3.

    The object key format is:
      logs/YYYY-MM-DD/<uuid>.json
    """
    today = datetime.date.today().isoformat()
    object_key = f"{prefix}{today}/{uuid.uuid4().hex}.json"

    s3.put_object(
        Bucket=LOG_BUCKET,
        Key=object_key,
        Body=json.dumps(log_item, ensure_ascii=False, indent=2),
        ContentType="application/json",
    )


from datetime import datetime, timezone

def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")

