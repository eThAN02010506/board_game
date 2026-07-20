.PHONY: test seed api web

test:
	PYTHONPATH=src python3 -m pytest -q

seed:
	PYTHONPATH=src python3 -m ai_kp.seeds.demo

api:
	uvicorn ai_kp.api.main:app --reload

web:
	cd apps/web && pnpm run dev
