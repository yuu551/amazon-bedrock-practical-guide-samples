import unittest
from unittest.mock import patch
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import ExportTraceServiceRequest
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from requests import Response
from observe_agent import langfuse_config


class LangfuseTests(unittest.TestCase):
    def test_otlp_payload_and_auth_without_remote_connection(self):
        endpoint, headers = langfuse_config({
            'LANGFUSE_BASE_URL': 'https://cloud.langfuse.com/',
            'LANGFUSE_PUBLIC_KEY': 'test-public', 'LANGFUSE_SECRET_KEY': 'test-secret',
        })
        response = Response()
        response.status_code = 200
        response._content = b''
        with patch('requests.Session.post', return_value=response) as post:
            exporter = OTLPSpanExporter(endpoint=endpoint, headers=headers)
            provider = TracerProvider()
            provider.add_span_processor(SimpleSpanProcessor(exporter))
            tracer = provider.get_tracer('test')
            with tracer.start_as_current_span('parent') as parent:
                with tracer.start_as_current_span('tool'):
                    pass
            provider.shutdown()
        self.assertEqual(post.call_count, 2)
        self.assertEqual(post.call_args.kwargs['url'], 'https://cloud.langfuse.com/api/public/otel/v1/traces')
        self.assertEqual(exporter._session.headers['x-langfuse-ingestion-version'], '4')
        self.assertTrue(exporter._session.headers['Authorization'].startswith('Basic '))
        child_payload = ExportTraceServiceRequest.FromString(post.call_args_list[0].kwargs['data'])
        child = child_payload.resource_spans[0].scope_spans[0].spans[0]
        self.assertEqual(child.name, 'tool')
        self.assertEqual(int.from_bytes(child.parent_span_id, 'big'), parent.get_span_context().span_id)

    def test_secret_not_in_configuration_error(self):
        for url in ['http://cloud.langfuse.com', 'https://test-secret@cloud.langfuse.com',
                    'https://cloud.langfuse.com/?token=test-secret']:
            with self.assertRaises(ValueError) as error:
                langfuse_config({'LANGFUSE_BASE_URL': url, 'LANGFUSE_SECRET_KEY': 'test-secret'})
            self.assertNotIn('test-secret', str(error.exception))


if __name__ == '__main__':
    unittest.main()
