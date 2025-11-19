# EC2 Self-Healing Demo (CloudWatch + SNS + Lambda + S3)

This project is a small **self-healing system for a single EC2 instance**.  
It demonstrates how to combine **CloudWatch Alarms, SNS, Lambda, and S3** to automatically react to high CPU usage and attempt a basic recovery action.

When the CPU utilization of a demo EC2 instance stays above a configured threshold, a CloudWatch Alarm transitions to `ALARM` state and triggers a Lambda function via SNS.  
The Lambda function logs the alarm details to S3 and, if appropriate, requests a reboot of the EC2 instance.

---

## 🧩 What this project demonstrates

- Creating a **CloudWatch Alarm** for EC2 `CPUUtilization`
- Wiring the alarm to an **SNS topic**
- Using **SNS → Lambda** as an event-driven pipeline
- Parsing the CloudWatch alarm payload in **Python (boto3)**
- Writing structured JSON logs to **S3** for later analysis
- Implementing a simple **EC2 self-healing action**:
  - If the instance is in `running` state when the alarm fires, request a reboot

This is a realistic Cloud Support / DevOps scenario and can be extended to more complex remediation workflows.

---

## 🏗️ Architecture

Event flow:

1. **EC2 instance**  
   - Demo instance running in `ap-southeast-2` (e.g. `t3.micro`)

2. **CloudWatch Metric & Alarm**
   - Metric: `CPUUtilization` (`AWS/EC2`, per-instance)
   - Alarm: transitions to `ALARM` when CPU is above a threshold (e.g. `> 0.1%` for demo)

3. **SNS Topic**
   - Alarm action: **ALARM → publish to SNS topic**
   - Topic fan-out:
     - Sends email to a human
     - Triggers the Lambda function

4. **Lambda Function (`ec2-self-healing-logger`)**
   - Triggered by SNS messages from the CloudWatch Alarm
   - Parses the alarm payload
   - Decides whether to reboot the EC2 instance
   - Writes a structured JSON log entry to S3

5. **S3 Log Bucket**
   - Bucket name example: `scott-ec2-self-healing-logs`
   - Stores one JSON file per alarm event under `logs/YYYY-MM-DD/`

> **In short:**  
> EC2 → CloudWatch Metric → CloudWatch Alarm → SNS → Lambda → S3 (+ EC2 reboot)

---

## 🧱 Components

- **Amazon EC2**
  - Demo instance that is being monitored and potentially rebooted

- **Amazon CloudWatch**
  - Metric: `CPUUtilization` for the demo instance  
  - Alarm: threshold-based, sends notifications via SNS

- **Amazon SNS**
  - Topic used as a fan-out to:
    - Email subscribers
    - The Lambda function

- **AWS Lambda**
  - Python 3.x runtime
  - Parses SNS events from CloudWatch Alarms
  - Uses `boto3` to talk to EC2 and S3

- **Amazon S3**
  - Stores structured alarm + action logs as JSON

---

## 📂 Repository structure

```text
ec2-self-healing-demo/
│
├── lambda/
│   └── ec2_self_healing_logger.py    
│
├── samples/
│   └── sample-alarm-log.json         
│
└── README.md
```

---

## 🧠 How the Lambda function works

The Lambda function is triggered by SNS notifications from a CloudWatch Alarm.
It processes each alarm event and decides whether a self-healing action should be taken.

**1. Receive the CloudWatch Alarm via SNS**

CloudWatch Alarm publishes a JSON payload to an SNS topic.
SNS then invokes the Lambda function with the following structure:

```json
event["Records"][0]["Sns"]["Message"]
```

This string is expected to be the standard CloudWatch Alarm JSON.

**2. Parse the Alarm Message**

The Lambda function reads and decodes the SNS message:

```python
alarm_message = json.loads(message_str)
```

If parsing fails, the raw message is recorded in S3 for debugging.

Extracted fields include:

- AlarmName
- NewStateValue (OK, ALARM, or INSUFFICIENT_DATA)
- NewStateReason
- Region
- Trigger → contains metric details and dimensions

**3. Determine Which EC2 Instance Was Affected**

The instance ID is derived as:

1. Default from environment variable INSTANCE_ID

2. Overridden if the CloudWatch Alarm payload contains a dimension named InstanceId

This ensures the Lambda function always knows exactly which instance to inspect or reboot.

**4. Self-Healing Decision Logic**

When the alarm enters the ALARM state:
```python
if new_state == "ALARM" and instance_id != "unknown":
```

The Lambda function:
1. Calls DescribeInstanceStatus to get the current EC2 state
2. If the instance is running, it performs a reboot:
```python
ec2.reboot_instances(InstanceIds=[instance_id])
```
3. Records a result such as:
- REBOOT_REQUESTED_from_running
- SKIPPED_state_stopped
- SKIPPED_state_pending
- ERROR_<ExceptionType>

This makes the healing logic transparent and easy to audit.

**5. Write a Structured Log Entry to S3**

Each alarm event (and the outcome of the self-healing decision) is saved as an individual JSON file in S3.

- Bucket: LOG_BUCKET
- Path format:
```
logs/YYYY-MM-DD/<uuid>.json
```
- Each log file contains:
```json
{
  "timestamp": "...",
  "alarm_name": "...",
  "new_state": "ALARM",
  "reason": "...",
  "instance_id": "i-xxxxxx",
  "region": "ap-southeast-2",
  "raw_alarm_message": { "..." },
  "action_taken": "REBOOT_REQUESTED_from_running"
}
```

This provides a clear audit trail showing:
- Why the alarm fired
- What the exact state was
- What the Lambda function decided
- Whether the EC2 instance was rebooted

## 🔐 IAM permissions

The Lambda execution role needs minimal permissions to do its job:

- Write logs to S3
- Query and reboot EC2

Example inline policy:
```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "AllowWriteLogsToS3",
      "Effect": "Allow",
      "Action": [
        "s3:PutObject"
      ],
      "Resource": "arn:aws:s3:::scott-ec2-self-healing-logs/*"
    },
    {
      "Sid": "AllowRebootDemoInstance",
      "Effect": "Allow",
      "Action": [
        "ec2:DescribeInstanceStatus",
        "ec2:RebootInstances"
      ],
      "Resource": "*"
    }
  ]
}
```

In a production environment you would restrict the EC2 Resource to a specific instance ARN.

## ⚙️ Configuration (environment variables)

The Lambda function uses the following environment variables:

- LOG_BUCKET: Name of the S3 bucket for logs
- INSTANCE_ID: The EC2 instance ID to protect

This makes it easy to change the target instance or bucket without modifying the code.

## 🚀 Possible extensions

Ideas for future improvements:

- Handle additional metrics (e.g. StatusCheckFailed, DiskReadOps)
- Only reboot after N consecutive ALARM evaluations
- Track a reboot_count per instance
- Send a nicely formatted email or Slack message with the log summary
- Move IAM permissions to Terraform / CloudFormation / CDK
