from argparse import Namespace
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import MagicMock, patch

from botocore.exceptions import ClientError

import common
import compare_kb_variants
import compare_metadata_modes
import generate_metadata_sidecars
import llm_document_parse


class CliInputValidationTests(TestCase):
    def test_document_parse_rejects_missing_directory_before_aws(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            args = Namespace(
                input_dir=root / "missing",
                output_dir=root / "output",
                model_id="test-model",
                force=False,
                workers=1,
            )

            with (
                patch.object(llm_document_parse, "parse_args", return_value=args),
                patch.object(llm_document_parse.boto3, "client") as client,
                self.assertRaisesRegex(
                    SystemExit,
                    "--input-dir must be an existing directory",
                ),
            ):
                llm_document_parse.main()

            client.assert_not_called()

    def test_document_parse_writes_markdown_and_reports_usage(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            input_dir = root / "input"
            output_dir = root / "output"
            input_dir.mkdir()
            source = input_dir / "料金表.pdf"
            source.write_bytes(b"test-pdf")
            (input_dir / "preview.png").write_bytes(b"not-supported")
            args = Namespace(
                input_dir=input_dir,
                output_dir=output_dir,
                model_id="test-model",
                force=False,
                workers=1,
            )
            runtime_client = MagicMock()
            runtime_client.converse.return_value = {
                "output": {
                    "message": {
                        "content": [{"text": "```markdown\n# 料金表\n\n| プラン | 料金 |\n|---|---|\n| Enterprise | 100円 |\n```"}]
                    }
                },
                "usage": {"inputTokens": 12, "outputTokens": 34},
            }

            with (
                patch.object(llm_document_parse, "parse_args", return_value=args),
                patch.object(
                    llm_document_parse.boto3,
                    "client",
                    return_value=runtime_client,
                ) as client,
                patch("builtins.print") as output,
            ):
                llm_document_parse.main()

            self.assertEqual(
                (output_dir / "料金表.md").read_text(encoding="utf-8"),
                "# 料金表\n\n| プラン | 料金 |\n|---|---|\n| Enterprise | 100円 |\n",
            )
            client.assert_called_once_with("bedrock-runtime", region_name="us-east-1")
            request = runtime_client.converse.call_args.kwargs
            self.assertEqual(request["modelId"], "test-model")
            self.assertEqual(
                request["messages"][0]["content"][0]["document"]["name"],
                "pdf",
            )
            self.assertTrue(
                any(
                    "input_tokens=12 output_tokens=34" in str(call)
                    for call in output.call_args_list
                )
            )

    def test_document_parse_skips_existing_output_unless_forced(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            input_dir = root / "input"
            output_dir = root / "output"
            input_dir.mkdir()
            output_dir.mkdir()
            (input_dir / "guide.pdf").write_bytes(b"test-pdf")
            output = output_dir / "guide.md"
            output.write_text("existing\n", encoding="utf-8")

            args = Namespace(
                input_dir=input_dir,
                output_dir=output_dir,
                model_id="test-model",
                force=False,
                workers=1,
            )
            with (
                patch.object(llm_document_parse, "parse_args", return_value=args),
                patch.object(llm_document_parse.boto3, "client") as client,
                patch("builtins.print"),
            ):
                llm_document_parse.main()

            self.assertEqual(output.read_text(encoding="utf-8"), "existing\n")
            client.assert_not_called()

    def test_document_parse_force_overwrites_existing_output(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            input_dir = root / "input"
            output_dir = root / "output"
            input_dir.mkdir()
            output_dir.mkdir()
            (input_dir / "guide.pdf").write_bytes(b"test-pdf")
            output = output_dir / "guide.md"
            output.write_text("existing\n", encoding="utf-8")

            args = Namespace(
                input_dir=input_dir,
                output_dir=output_dir,
                model_id="test-model",
                force=True,
                workers=1,
            )
            with (
                patch.object(llm_document_parse, "parse_args", return_value=args),
                patch.object(
                    llm_document_parse.boto3,
                    "client",
                    return_value=MagicMock(),
                ),
                patch.object(
                    llm_document_parse,
                    "convert_document",
                    return_value=("converted\n", 10, 20),
                ) as convert,
                patch("builtins.print"),
            ):
                llm_document_parse.main()

            self.assertEqual(output.read_text(encoding="utf-8"), "converted\n")
            convert.assert_called_once()

    def test_document_parse_parallel_matches_sequential_output(self) -> None:
        def fake_converse(**request):
            name = request["messages"][0]["content"][0]["document"]["name"]
            return {
                "output": {"message": {"content": [{"text": f"# {name}"}]}},
                "usage": {"inputTokens": 1, "outputTokens": 2},
            }

        results = {}
        for workers in (1, 3):
            with TemporaryDirectory() as temp_dir:
                root = Path(temp_dir)
                input_dir = root / "input"
                output_dir = root / "output"
                (input_dir / "nested").mkdir(parents=True)
                for name in ("a.pdf", "b.pdf", "c.pdf", "nested/d.pdf"):
                    (input_dir / name).write_bytes(b"test-pdf")
                args = Namespace(
                    input_dir=input_dir,
                    output_dir=output_dir,
                    model_id="test-model",
                    force=False,
                    workers=workers,
                )
                runtime_client = MagicMock()
                runtime_client.converse.side_effect = fake_converse
                with (
                    patch.object(llm_document_parse, "parse_args", return_value=args),
                    patch.object(
                        llm_document_parse.boto3,
                        "client",
                        return_value=runtime_client,
                    ) as client,
                    patch("builtins.print"),
                ):
                    llm_document_parse.main()

                client.assert_called_once()
                self.assertEqual(runtime_client.converse.call_count, 4)
                results[workers] = {
                    str(path.relative_to(output_dir)): path.read_text(encoding="utf-8")
                    for path in sorted(output_dir.rglob("*.md"))
                }

        self.assertEqual(len(results[1]), 4)
        self.assertEqual(results[1], results[3])

    def test_document_parse_retries_throttling_with_backoff(self) -> None:
        throttled = ClientError(
            {"Error": {"Code": "ThrottlingException", "Message": "slow down"}},
            "Converse",
        )
        runtime_client = MagicMock()
        runtime_client.converse.side_effect = [throttled, throttled, {"ok": True}]

        with (
            patch.object(llm_document_parse.time, "sleep") as sleep,
            patch("builtins.print"),
        ):
            response = llm_document_parse.converse_with_retry(
                runtime_client, modelId="test-model"
            )

        self.assertEqual(response, {"ok": True})
        self.assertEqual(runtime_client.converse.call_count, 3)
        self.assertEqual([c.args[0] for c in sleep.call_args_list], [2.0, 4.0])

    def test_document_parse_does_not_retry_other_errors(self) -> None:
        denied = ClientError(
            {"Error": {"Code": "AccessDeniedException", "Message": "no"}},
            "Converse",
        )
        runtime_client = MagicMock()
        runtime_client.converse.side_effect = denied

        with (
            patch.object(llm_document_parse.time, "sleep") as sleep,
            self.assertRaises(ClientError),
        ):
            llm_document_parse.converse_with_retry(runtime_client, modelId="m")

        runtime_client.converse.assert_called_once()
        sleep.assert_not_called()

    def test_compare_rejects_missing_document_before_creating_aws_client(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            schema_file = root / "schema.json"
            schema_file.write_text("{}", encoding="utf-8")
            args = Namespace(
                model_id="test-model",
                document=root / "missing.md",
                schema_file=schema_file,
                region="us-east-1",
                output_dir=root / "output",
            )

            with (
                patch.object(compare_metadata_modes, "parse_args", return_value=args),
                patch.object(compare_metadata_modes.boto3, "client") as client,
                self.assertRaisesRegex(SystemExit, "--document must be an existing file"),
            ):
                compare_metadata_modes.main()

            client.assert_not_called()

    def test_sidecar_generation_rejects_missing_directory_before_aws(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            schema_file = root / "schema.json"
            schema_file.write_text("{}", encoding="utf-8")
            args = Namespace(
                model_id="test-model",
                input_dir=root / "missing",
                schema_file=schema_file,
                region="us-east-1",
                output_dir=root / "output",
                pattern="**/*",
                overwrite=False,
            )

            with (
                patch.object(generate_metadata_sidecars, "parse_args", return_value=args),
                patch.object(generate_metadata_sidecars.boto3, "client") as client,
                self.assertRaisesRegex(SystemExit, "--input-dir must be an existing directory"),
            ):
                generate_metadata_sidecars.main()

            client.assert_not_called()

    def test_readme_example_inputs_exist(self) -> None:
        chapter_dir = Path(__file__).resolve().parent

        self.assertTrue(
            (
                chapter_dir.parent
                / "02/sample-data/policies/SLAポリシー2026年版.md"
            ).is_file()
        )
        self.assertTrue(
            (chapter_dir.parent / "02/sample-data/policies").is_dir()
        )

    def test_compare_reads_readme_example_document(self) -> None:
        chapter_dir = Path(__file__).resolve().parent
        document = (
            chapter_dir.parent / "02/sample-data/policies/SLAポリシー2026年版.md"
        )
        schema_file = chapter_dir / "data/metadata_schema.json"

        with TemporaryDirectory() as temp_dir:
            args = Namespace(
                model_id="test-model",
                document=document,
                schema_file=schema_file,
                region="us-east-1",
                output_dir=Path(temp_dir),
            )

            with (
                patch.object(compare_metadata_modes, "parse_args", return_value=args),
                patch.object(compare_metadata_modes.boto3, "client"),
                patch.object(
                    compare_metadata_modes,
                    "generate_metadata_payload",
                    return_value={"metadataAttributes": {}},
                ) as generate,
                patch("builtins.print"),
            ):
                compare_metadata_modes.main()

            self.assertEqual(generate.call_count, 2)
            for call in generate.call_args_list:
                self.assertEqual(call.kwargs["document_bytes"], document.read_bytes())

    def test_sidecar_generation_processes_readme_example_directory(self) -> None:
        chapter_dir = Path(__file__).resolve().parent
        input_dir = chapter_dir.parent / "02/sample-data/policies"

        with TemporaryDirectory() as temp_dir:
            output_dir = Path(temp_dir)
            args = Namespace(
                model_id="test-model",
                input_dir=input_dir,
                schema_file=chapter_dir / "data/metadata_schema.json",
                region="us-east-1",
                output_dir=output_dir,
                pattern="**/*",
                overwrite=False,
            )

            with (
                patch.object(generate_metadata_sidecars, "parse_args", return_value=args),
                patch.object(generate_metadata_sidecars.boto3, "client"),
                patch.object(
                    generate_metadata_sidecars,
                    "generate_metadata_payload",
                    return_value={"metadataAttributes": {}},
                ) as generate,
                patch("builtins.print"),
            ):
                generate_metadata_sidecars.main()

            source_documents = sorted(input_dir.glob("*.md"))
            self.assertEqual(generate.call_count, len(source_documents))
            for source in source_documents:
                self.assertTrue(
                    (output_dir / f"{source.name}.metadata.json").is_file()
                )


HAIKU_5_5_IDS = (
    "us.anthropic.claude-haiku-5-5",
    "jp.anthropic.claude-haiku-5-5",
    "global.anthropic.claude-haiku-5-5",
    "arn:aws:bedrock:us-east-1:123456789012:inference-profile/us.anthropic.claude-haiku-5-5",
)
HAIKU_4_5_ID = "us.anthropic.claude-haiku-4-5-20251001-v1:0"


class ModelSettingsTests(TestCase):
    """Haiku 5.5には温度を送らずKBのプロンプトを渡し、Haiku 4.5は従来どおりにする。"""

    def test_haiku_5_5_omits_temperature(self) -> None:
        for model_id in HAIKU_5_5_IDS:
            with self.subTest(model_id=model_id):
                self.assertEqual(
                    common.converse_inference_config(model_id, 1200, 0),
                    {"maxTokens": 4096},
                )
        self.assertEqual(
            common.converse_inference_config(HAIKU_5_5_IDS[0], 8192, 0.0),
            {"maxTokens": 8192},
        )

    def test_haiku_4_5_keeps_temperature(self) -> None:
        self.assertEqual(
            common.converse_inference_config(HAIKU_4_5_ID, 1200, 0),
            {"maxTokens": 1200, "temperature": 0},
        )
        self.assertEqual(common.knowledge_base_prompts(HAIKU_4_5_ID), {})

    def test_kb_request_includes_both_prompts_for_haiku_5_5(self) -> None:
        variant = compare_kb_variants.Variant(name="v", knowledge_base_id="KB")
        for model_id in HAIKU_5_5_IDS:
            args = Namespace(
                number_of_results=5,
                search_type="DEFAULT",
                use_question_filter=False,
                model_arn=model_id,
            )
            with self.subTest(model_id=model_id):
                config = compare_kb_variants.build_request(
                    {"question": "q"}, variant, args
                )["retrieveAndGenerateConfiguration"]["knowledgeBaseConfiguration"]
                generation = config["generationConfiguration"]
                orchestration = config["orchestrationConfiguration"]
                for placeholder in ("$query$", "$search_results$", "$output_format_instructions$"):
                    self.assertIn(placeholder, generation["promptTemplate"]["textPromptTemplate"])
                for placeholder in ("$conversation_history$", "$query$", "$output_format_instructions$"):
                    self.assertIn(placeholder, orchestration["promptTemplate"]["textPromptTemplate"])
                self.assertNotIn("temperature", generation["inferenceConfig"]["textInferenceConfig"])

    def test_kb_request_unchanged_for_haiku_4_5(self) -> None:
        variant = compare_kb_variants.Variant(name="v", knowledge_base_id="KB")
        args = Namespace(
            number_of_results=5,
            search_type="DEFAULT",
            use_question_filter=False,
            model_arn=HAIKU_4_5_ID,
        )
        config = compare_kb_variants.build_request(
            {"question": "q"}, variant, args
        )["retrieveAndGenerateConfiguration"]["knowledgeBaseConfiguration"]
        self.assertEqual(
            config,
            {
                "knowledgeBaseId": "KB",
                "modelArn": HAIKU_4_5_ID,
                "retrievalConfiguration": {
                    "vectorSearchConfiguration": {"numberOfResults": 5},
                },
            },
        )
