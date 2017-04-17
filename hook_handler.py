import json
import logging
import os
import sys

here = os.path.dirname(os.path.realpath(__file__))
sys.path.append(os.path.join(here, "vendored"))
import requests

logger = logging.getLogger()
logger.setLevel(logging.INFO)


def handler(event, context):

    request_body = json.loads(event["body"])

    if "buildUrl" not in request_body:
        return {
            "statusCode": 400,
            "body": json.dumps({
                "error": "buildUrl must be present in request"
            })
        }

    if "jenkinsUrl" not in request_body:
        return {
            "statusCode": 400,
            "body": json.dumps({
                "error": "jenkinsUrl must be present in request"
            })
        }

    if "buildVersion" not in request_body:
        return {
            "statusCode": 400,
            "body": json.dumps({
                "error": "buildVersion must be present in request"
            })
        }

    build_url = request_body["buildUrl"]
    jenkins_url = request_body["jenkinsUrl"]
    build_version = request_body["buildVersion"]

    logger.info("Received request for build version " + build_version)

    data = {
        "text": "Build " + build_version + " requests deployment",
        "attachments": [
            {
                "text": "Proceed with deployment?",
                "color": "#3AA3E3",
                "attachment_type": "default",
                "callback_id": "approve",
                "actions": [
                    {
                        "name": "approve",
                        "text": "Proceed",
                        "type": "button",
                        "value": str.format("true|{}|{}|{}",
                                            build_url,
                                            jenkins_url,
                                            build_version),
                        "style": "primary"
                    },
                    {
                        "name": "approve",
                        "text": "Abort",
                        "type": "button",
                        "value": str.format("false|{}|{}|{}",
                                            build_url,
                                            jenkins_url,
                                            build_version),
                        "style": "danger"
                    }
                ],

            }
        ]}
    res = requests.post(os.environ["slackWebhook"],
                        data=json.dumps(data),
                        headers={"Content-type": "application/json"})

    response = {
        "statusCode": 200
    }

    return response
