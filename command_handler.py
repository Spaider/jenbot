import json
import logging
import os
import sys
import time
import urlparse

import boto3

here = os.path.dirname(os.path.realpath(__file__))
sys.path.append(os.path.join(here, "vendored"))
import requests

# Lambda bootstrapper installs its logging handler which prevents setting
# our own logging config. Remove all handlers at first
root = logging.getLogger()
if root.handlers:
    for handler in root.handlers:
        root.removeHandler(handler)
logging.basicConfig(level=logging.INFO, format="%(levelname)s \"%(message)s\"")
logger = logging.getLogger()

JENKINS_USER = None
JENKINS_PASSWORD = None
SLACK_TOKEN = None

requests.packages.urllib3.disable_warnings()


def init_globals():
    global JENKINS_USER
    global JENKINS_PASSWORD
    global SLACK_TOKEN

    if "jenkinsUser" not in os.environ:
        raise Exception("jenkinsUser environment variable is not defined")
    JENKINS_USER = os.environ["jenkinsUser"]

    if "jenkinsPassword" not in os.environ:
        raise Exception("jenkinsPassword environment variable is not defined")
    JENKINS_PASSWORD = os.environ["jenkinsPassword"]

    if "slackToken" not in os.environ:
        raise Exception("slackToken environment variable is not defined")
    SLACK_TOKEN = os.environ["slackToken"]


def get_payload(body_str):
    request_body = urlparse.parse_qs(body_str)

    if "payload" not in request_body:
        raise Exception("Request must contain 'payload' field")

    payload_str = request_body["payload"][0]
    return json.loads(payload_str)


def get_approval_status(payload):
    """ 
    Gets data from command received from Slack
    """
    approve_action = next(a for a in payload["actions"] if a["name"] == "approve")
    if approve_action is None:
        raise Exception("Request must contain 'approve' action")
    action_data = approve_action["value"].split("|")
    return {
        "approved": action_data[0].lower() == "true",
        "buildUrl": action_data[1],
        "jenkinsUrl": action_data[2],
        "buildVersion": action_data[3]
    }


def get_jenkins_crumb(jenkins_url):
    logger.info("Getting Jenkins crumb from " + jenkins_url)
    res = requests.get(jenkins_url + "crumbIssuer/api/json",
                       auth=(JENKINS_USER, JENKINS_PASSWORD),
                       verify=False)
    crumb = res.json()["crumb"]
    logger.info("Crumb is " + crumb)
    return crumb


def get_pending_input_url(build_url, crumb):
    """
    Returns URLs for pending input submission
    :param build_url: API entry point for build 
    :param crumb: Jenkins crumb
    :return: None if build is not waiting for input, API URLs for proceeding and aborting the build
    """

    logger.info("Getting build status")
    res = requests.get(build_url + "wfapi",
                       auth=(JENKINS_USER, JENKINS_PASSWORD),
                       verify=False,
                       headers={
                           "Jenkins-Crumb": crumb
                       })
    response = res.json()
    if response["status"] != "PAUSED_PENDING_INPUT":
        logger.info("Build is not waiting for input, status is " + response["status"])
        return None

    logger.info("Getting proceed URL")
    res = requests.post(build_url + "wfapi/pendingInputActions",
                        auth=(JENKINS_USER, JENKINS_PASSWORD),
                        verify=False,
                        headers={
                            "Jenkins-Crumb": crumb
                        })
    response = res.json()
    input_id = response[0]["id"]
    return (
        str.format("{}wfapi/inputSubmit?inputId={}", build_url, input_id),  # Proceed URL
        str.format("{}input/{}/abort", build_url, input_id))  # Abort URL


def approve_build(proceed_url, crumb):
    logger.info("Approving build")
    res = requests.post(proceed_url,
                        auth=(JENKINS_USER, JENKINS_PASSWORD),
                        verify=False,
                        headers={
                            "Jenkins-Crumb": crumb,
                            "Content-Type": "application/x-www-form-urlencoded"
                        },
                        data={
                            "Proceed": "proceed",
                            "json": json.dumps({
                                "parameter": []
                            })
                        })
    return res.ok


def reject_build(abort_url, crumb):
    logger.info("Rejecting build")
    res = requests.post(abort_url,
                        auth=(JENKINS_USER, JENKINS_PASSWORD),
                        verify=False,
                        headers={
                            "Jenkins-Crumb": crumb
                        },
                        data={})
    return res.ok


def post_response_to_slack(response_url, body):
    res = requests.post(response_url, json=body)
    if not res.ok:
        logger.error("Error posting response to Slack: %s", res.text)


def handler(event, context):
    try:
        init_globals()

        payload = get_payload(event["body"])

        # Check Slack token for validity
        if "token" not in payload or payload["token"] != SLACK_TOKEN:
            raise Exception("token is not valid")

        if "response_url" not in payload:
            raise Exception("response_url not found in request")

        # Initial request received. Response with HTTP 200 and
        # call ourself again
        if "recursive" not in event:
            logger.info("Initial call")
            session = boto3.session.Session()
            lambda_cli = session.client("lambda")
            lambda_cli.invoke_async(FunctionName=context.function_name,
                                    InvokeArgs=json.dumps(dict({"recursive": True}, **event)))
            return {
                "statusCode": 200
            }

        response_url = payload["response_url"]
        user_name = payload["user"]["name"]
        user_id = payload["user"]["id"]
        logger.info("Received command request from " + user_name)

        command_request = get_approval_status(payload)
        build_version = command_request["buildVersion"]
        approval_status = command_request["approved"]
        crumb = get_jenkins_crumb(command_request["jenkinsUrl"])
        pending_input_urls = get_pending_input_url(command_request["buildUrl"], crumb)
        if pending_input_urls is None:
            post_response_to_slack(
                response_url,
                {
                    "attachments": [{
                        "text": "Build %s is not waiting for input" % build_version,
                        "color": "warning",
                        "ts": int(round(time.time()))
                    }]
                })
            return {}

        if approval_status:
            approve_build(pending_input_urls[0], crumb)
        else:
            reject_build(pending_input_urls[1], crumb)

        approve_msg = str.format("Version {} deployment *{}* by <@{}|{}>",
                                 build_version,
                                 "approved" if approval_status else "declined",
                                 user_name,
                                 user_id)

        post_response_to_slack(
            response_url,
            {
                "attachments": [{
                    "text": approve_msg,
                    "color": "good" if approval_status else "danger",
                    "mrkdwn_in": ["text"],
                    "ts": int(round(time.time()))
                }]
            })
    except Exception as e:
        logger.error(e.message, exc_info=1)

    return {}
