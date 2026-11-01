PY ?= python3

.PHONY: assets sample sample-vertical sample-csv template test clean
assets:            ## ダミー素材を生成
	$(PY) tools/make_dummy_assets.py
sample: assets     ## 30秒サンプル (16:9 1080p)
	$(PY) -m vedit render examples/sample_30s.json
sample-vertical: assets  ## 同じカット表を縦動画 (9:16) で
	$(PY) -m vedit render examples/sample_30s.json --aspect 9:16
sample-csv: assets ## CSV 版を 540p プレビューで
	$(PY) -m vedit render examples/sample_30s.csv --preview
template: assets   ## 90秒テンプレート
	$(PY) -m vedit render examples/template_90s.json
test:
	$(PY) -m unittest discover -s tests
clean:
	rm -rf out examples/assets
