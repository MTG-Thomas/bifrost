#!/bin/bash -eu

cd "$SRC/bifrost"

pip3 install --target "$OUT/deps" -r .clusterfuzzlite/requirements.txt
cp -R api "$OUT/api"

for fuzzer in api/fuzz/atheris_targets/*_fuzzer.py; do
  fuzzer_basename=$(basename -s .py "$fuzzer")

  cat > "$OUT/$fuzzer_basename" <<EOF
#!/bin/sh
# LLVMFuzzerTestOneInput for ClusterFuzzLite target detection.
this_dir=\$(dirname "\$0")
PYTHONPATH="\$this_dir/deps:\$this_dir/api" python3 "\$this_dir/api/fuzz/cfl_entrypoint.py" "$fuzzer_basename" "\$@"
EOF
  chmod +x "$OUT/$fuzzer_basename"
done

python3 - <<'PY'
import os
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

out_dir = Path(os.environ["OUT"])
corpus_root = Path("api/fuzz/corpora")
target_names = {
    "cron-parser": "cron_parser_fuzzer",
    "editor-search": "editor_search_fuzzer",
    "webhook-request": "webhook_request_fuzzer",
}

for corpus_name, fuzzer_name in target_names.items():
    corpus_dir = corpus_root / corpus_name
    zip_path = out_dir / f"{fuzzer_name}_seed_corpus.zip"
    with ZipFile(zip_path, "w", ZIP_DEFLATED) as archive:
        for path in sorted(corpus_dir.iterdir()):
            if path.is_file():
                archive.write(path, path.name)
PY
