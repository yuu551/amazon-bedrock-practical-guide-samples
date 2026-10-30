"""検証専用のCloudFormationテンプレート。DBへのTCP接続は公開しない。"""

import json


def template(engine_version="16.14"):
    def ref(name):
        return {"Ref": name}

    def attr(name, key):
        return {"Fn::GetAtt": [name, key]}

    def sub(value):
        return {"Fn::Sub": value}

    def resource(kind, **props):
        return {"Type": "AWS::" + kind, "Properties": props}

    r = {}
    r["Vpc"] = resource(
        "EC2::VPC",
        CidrBlock="10.87.0.0/16",
        EnableDnsSupport=True,
        EnableDnsHostnames=True,
    )
    for i in range(2):
        r[f"Subnet{i}"] = resource(
            "EC2::Subnet",
            VpcId=ref("Vpc"),
            CidrBlock=f"10.87.{i}.0/24",
            AvailabilityZone={"Fn::Select": [i, {"Fn::GetAZs": ""}]},
            MapPublicIpOnLaunch=False,
        )
    r["Subnets"] = resource(
        "RDS::DBSubnetGroup",
        DBSubnetGroupDescription="Aurora KB isolated subnets",
        SubnetIds=[ref("Subnet0"), ref("Subnet1")],
    )
    r["SecurityGroup"] = resource(
        "EC2::SecurityGroup",
        GroupDescription="Data API only, no inbound TCP",
        VpcId=ref("Vpc"),
    )
    r["Cluster"] = resource(
        "RDS::DBCluster",
        Engine="aurora-postgresql",
        EngineVersion=engine_version,
        EngineMode="provisioned",
        DatabaseName="knowledge",
        MasterUsername="dbadmin",
        ManageMasterUserPassword=True,
        EnableHttpEndpoint=True,
        StorageEncrypted=True,
        BackupRetentionPeriod=1,
        DeletionProtection=False,
        DBSubnetGroupName=ref("Subnets"),
        VpcSecurityGroupIds=[ref("SecurityGroup")],
        ServerlessV2ScalingConfiguration={
            "MinCapacity": 0,
            "MaxCapacity": 2,
            "SecondsUntilAutoPause": 3600,
        },
    )
    r["Cluster"].update(DeletionPolicy="Delete", UpdateReplacePolicy="Delete")
    r["Writer"] = resource(
        "RDS::DBInstance",
        Engine="aurora-postgresql",
        DBClusterIdentifier=ref("Cluster"),
        DBInstanceClass="db.serverless",
        PubliclyAccessible=False,
        AutoMinorVersionUpgrade=False,
    )
    r["Writer"].update(DeletionPolicy="Delete", UpdateReplacePolicy="Delete")
    r["KbSecret"] = resource(
        "SecretsManager::Secret",
        Description="Aurora KB dedicated database user",
        GenerateSecretString={
            "SecretStringTemplate": '{"username":"bedrock_user"}',
            "GenerateStringKey": "password",
            "PasswordLength": 32,
            "ExcludePunctuation": True,
        },
    )
    r["Bucket"] = resource(
        "S3::Bucket",
        PublicAccessBlockConfiguration={
            "BlockPublicAcls": True,
            "BlockPublicPolicy": True,
            "IgnorePublicAcls": True,
            "RestrictPublicBuckets": True,
        },
        BucketEncryption={
            "ServerSideEncryptionConfiguration": [
                {"ServerSideEncryptionByDefault": {"SSEAlgorithm": "AES256"}}
            ]
        },
    )
    statements = [
        {
            "Effect": "Allow",
            "Action": ["bedrock:InvokeModel"],
            "Resource": sub(
                "arn:${AWS::Partition}:bedrock:${AWS::Region}::foundation-model/amazon.titan-embed-text-v2:0"
            ),
        },
        {
            "Effect": "Allow",
            "Action": ["s3:ListBucket"],
            "Resource": attr("Bucket", "Arn"),
        },
        {
            "Effect": "Allow",
            "Action": ["s3:GetObject"],
            "Resource": sub("${Bucket.Arn}/documents/*"),
        },
        {
            "Effect": "Allow",
            "Action": [
                "rds:DescribeDBClusters",
                "rds-data:ExecuteStatement",
                "rds-data:BatchExecuteStatement",
            ],
            "Resource": attr("Cluster", "DBClusterArn"),
        },
        {
            "Effect": "Allow",
            "Action": ["secretsmanager:GetSecretValue"],
            "Resource": ref("KbSecret"),
        },
    ]
    r["KbRole"] = resource(
        "IAM::Role",
        AssumeRolePolicyDocument={
            "Version": "2012-10-17",
            "Statement": [
                {
                    "Effect": "Allow",
                    "Principal": {"Service": "bedrock.amazonaws.com"},
                    "Action": "sts:AssumeRole",
                    "Condition": {
                        "StringEquals": {"aws:SourceAccount": ref("AWS::AccountId")},
                        "ArnLike": {
                            "aws:SourceArn": sub(
                                "arn:${AWS::Partition}:bedrock:${AWS::Region}:${AWS::AccountId}:knowledge-base/*"
                            )
                        },
                    },
                }
            ],
        },
        Policies=[
            {
                "PolicyName": "DedicatedAuroraKb",
                "PolicyDocument": {"Version": "2012-10-17", "Statement": statements},
            }
        ],
    )
    outputs = {
        "ClusterArn": attr("Cluster", "DBClusterArn"),
        "ClusterId": ref("Cluster"),
        "MasterSecretArn": attr("Cluster", "MasterUserSecret.SecretArn"),
        "KbSecretArn": ref("KbSecret"),
        "Bucket": ref("Bucket"),
        "KbRoleArn": attr("KbRole", "Arn"),
    }
    return {
        "AWSTemplateFormatVersion": "2010-09-09",
        "Description": "Disposable Aurora pgvector Knowledge Bases supplement",
        "Resources": r,
        "Outputs": {k: {"Value": v} for k, v in outputs.items()},
    }


if __name__ == "__main__":
    print(json.dumps(template(), indent=2))
