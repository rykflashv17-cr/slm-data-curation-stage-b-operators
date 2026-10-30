"""Independent legal-MCAP matrix; uses installed mcap.Writer and local validator.

Run from the repository with PYTHONPATH=. . No numerical engine is involved.
Files are generated only inside a temporary directory, then removed.
"""
import json
from itertools import product
from pathlib import Path
from tempfile import TemporaryDirectory

from mcap.writer import CompressionType, IndexType, Writer
from stage_b_mcap_v4.integrity import validate_mcap

FLAGS = [
    "repeat_channels", "repeat_schemas", "use_chunking", "use_statistics",
    "use_summary_offsets", "enable_crcs", "enable_data_crcs",
]

def main():
    passed, errors = 0, []
    with TemporaryDirectory(prefix="independent-mcap-matrix-") as temporary:
        path = Path(temporary) / "matrix.mcap"
        variants = product(product((False, True), repeat=7), list(CompressionType),
                           (IndexType.NONE, IndexType.ALL))
        for values, codec, index in variants:
            options = dict(zip(FLAGS, values))
            options.update(compression=codec, index_types=index)
            try:
                with path.open("wb") as stream:
                    writer = Writer(stream, **options)
                    writer.start()
                    schema = writer.register_schema("schema", "raw", b"abc")
                    channel = writer.register_channel("/robot", "raw", schema)
                    for i in range(3):
                        writer.add_message(channel, i, b"payload", i)
                    writer.add_attachment(0, 0, "a", "application/octet-stream", b"abc")
                    writer.add_metadata("a", {"b": "c"})
                    writer.finish()
                validate_mcap(path)
                passed += 1
            except Exception as exc:
                errors.append({"flags": dict(zip(FLAGS, values)), "compression": codec.name,
                               "index_types": index.name, "error": f"{type(exc).__name__}: {exc}"})
    result = {"expected": 768, "passed": passed, "failed": len(errors),
              "boolean_flags": FLAGS, "compression": [c.name for c in CompressionType],
              "index_types": ["NONE", "ALL"], "errors": errors}
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if passed == 768 and not errors else 1

if __name__ == "__main__":
    raise SystemExit(main())
