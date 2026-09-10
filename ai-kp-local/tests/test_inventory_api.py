import tempfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from fastapi.testclient import TestClient

from ai_kp.api.main import create_app
from ai_kp.bootstrap.settings import Settings
from tests.support_investigators import (
    coc7_sheet,
    confirm_current_session_zero_sync,
    create_approved_player_sync,
)


def bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def test_inventory_ownership_hidden_truth_consumption_and_currency_are_atomic() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "inventory.sqlite3"
        app = create_app(
            Settings(
                db_path=db_path,
                admin_token="inventory-test-admin",
                local_admin_enabled=False,
            )
        )
        with TestClient(app) as client:
            admin_headers = {"X-AI-KP-Admin-Token": "inventory-test-admin"}
            campaign = client.post(
                "/campaigns",
                headers=admin_headers,
                json={"title": "物品账本验收", "system": "coc7"},
            ).json()
            session = client.post(
                f"/campaigns/{campaign['id']}/sessions",
                headers=admin_headers,
                json={"kp_display_name": "账本 KP"},
            ).json()
            kp_headers = bearer(session["access_token"])
            first = create_approved_player_sync(
                client,
                campaign=campaign,
                session=session,
                kp_headers=kp_headers,
                display_name="玩家甲",
                sheet=coc7_sheet("甲"),
            )
            second = create_approved_player_sync(
                client,
                campaign=campaign,
                session=session,
                kp_headers=kp_headers,
                display_name="玩家乙",
                sheet=coc7_sheet("乙"),
            )
            observer = client.post(
                "/sessions/join",
                json={
                    "join_code": session["join_code"],
                    "display_name": "观战者",
                    "role": "observer",
                },
            ).json()
            confirm_current_session_zero_sync(
                client,
                campaign_id=campaign["id"],
                member_headers=(kp_headers, first["headers"], second["headers"]),
            )

            created = client.post(
                f"/campaigns/{campaign['id']}/inventory/items",
                headers=kp_headers,
                json={
                    "command_id": "create-unique-key-0001",
                    "item_type": "unique",
                    "public_name": "无标记铜钥匙",
                    "public_description": "一把外观普通的旧钥匙。",
                    "quantity": 1,
                    "is_unique": True,
                    "holder_kind": "loot",
                    "holder_id": "loot-table",
                    "hidden_properties": {"opens": "sealed-archive"},
                    "source_refs": [{"kind": "event", "id": "fixture-source"}],
                },
            )
            assert created.status_code == 200, created.text
            item = created.json()["item"]

            first_pickup = client.post(
                f"/inventory/items/{item['id']}/commands",
                headers=first["headers"],
                json={
                    "command_id": "pickup-unique-key-first",
                    "command_type": "pickup",
                    "expected_version": item["version"],
                },
            )
            assert first_pickup.status_code == 200, first_pickup.text
            owned = first_pickup.json()["item"]
            assert owned["holder_id"] == first["investigator"]["id"]
            assert "hidden_properties" not in first_pickup.text

            losing_race = client.post(
                f"/inventory/items/{item['id']}/commands",
                headers=second["headers"],
                json={
                    "command_id": "pickup-unique-key-second",
                    "command_type": "pickup",
                    "expected_version": item["version"],
                },
            )
            assert losing_race.status_code == 409

            other_view = client.get(
                f"/campaigns/{campaign['id']}/inventory",
                headers=second["headers"],
            ).json()
            assert other_view["items"] == []
            observer_view = client.get(
                f"/campaigns/{campaign['id']}/inventory",
                headers=bearer(observer["access_token"]),
            ).json()
            assert observer_view["items"] == []
            assert observer_view["balances"] == []

            revealed = client.post(
                f"/inventory/items/{item['id']}/commands",
                headers=kp_headers,
                json={
                    "command_id": "reveal-unique-key-first",
                    "command_type": "reveal",
                    "expected_version": owned["version"],
                    "reveal_member_id": first["bundle"]["member"]["id"],
                },
            ).json()["item"]
            assert revealed["hidden_properties"] == {"opens": "sealed-archive"}
            first_view = client.get(
                f"/campaigns/{campaign['id']}/inventory",
                headers=first["headers"],
            ).json()
            assert first_view["items"][0]["revealed_properties"] == {
                "opens": "sealed-archive"
            }

            character_state = client.get(
                f"/campaigns/{campaign['id']}/investigators/{first['investigator']['id']}/coc7/state",
                headers=first["headers"],
            ).json()["state"]
            damaged = client.post(
                f"/campaigns/{campaign['id']}/investigators/{first['investigator']['id']}/coc7/commands",
                headers=kp_headers,
                json={
                    "command_id": "inventory-fixture-damage",
                    "expected_version": character_state["state_version"],
                    "command_type": "damage",
                    "payload": {"damage": 2},
                    "visibility": "table",
                },
            )
            assert damaged.status_code == 200, damaged.text

            consumables = client.post(
                f"/campaigns/{campaign['id']}/inventory/items",
                headers=kp_headers,
                json={
                    "command_id": "create-consumables-0001",
                    "item_type": "consumable",
                    "public_name": "绷带",
                    "quantity": 2,
                    "is_unique": False,
                    "holder_kind": "investigator",
                    "holder_id": first["investigator"]["id"],
                    "use_effect": {
                        "kind": "ruleset_character_command",
                        "command_type": "first_aid",
                        "payload": {"passed": True},
                    },
                    "reason": "测试用 KP 手工授予的基础消耗品",
                },
            ).json()["item"]
            consume_payload = {
                "command_id": "consume-bandage-0001",
                "command_type": "consume",
                "expected_version": consumables["version"],
                "quantity": 1,
            }
            consumed = client.post(
                f"/inventory/items/{consumables['id']}/commands",
                headers=first["headers"],
                json=consume_payload,
            ).json()
            assert consumed["item"]["quantity"] == 1
            assert consumed["effect"]["state"]["current_hp"] == 9
            replay = client.post(
                f"/inventory/items/{consumables['id']}/commands",
                headers=first["headers"],
                json=consume_payload,
            ).json()
            assert replay["idempotent_replay"] is True
            assert replay["item"]["quantity"] == 1
            assert replay["effect"]["state"]["current_hp"] == 9

            granted = client.post(
                f"/campaigns/{campaign['id']}/currency/transfers",
                headers=kp_headers,
                json={
                    "command_id": "grant-currency-0001",
                    "from_kind": None,
                    "from_id": None,
                    "to_kind": "investigator",
                    "to_id": first["investigator"]["id"],
                    "currency_code": "USD_CENTS",
                    "amount_minor": 1000,
                    "expected_to_version": None,
                },
            )
            assert granted.status_code == 200, granted.text
            account = granted.json()["result"]["to"]
            spend = {
                "from_kind": "investigator",
                "from_id": first["investigator"]["id"],
                "to_kind": "party",
                "to_id": campaign["id"],
                "currency_code": "USD_CENTS",
                "amount_minor": 700,
                "expected_from_version": account["version"],
                "expected_to_version": None,
            }
            paid = client.post(
                f"/campaigns/{campaign['id']}/currency/transfers",
                headers=first["headers"],
                json={"command_id": "spend-currency-first", **spend},
            )
            assert paid.status_code == 200, paid.text
            stale_spend = client.post(
                f"/campaigns/{campaign['id']}/currency/transfers",
                headers=first["headers"],
                json={"command_id": "spend-currency-second", **spend},
            )
            assert stale_spend.status_code == 409
            balances = client.get(
                f"/campaigns/{campaign['id']}/inventory",
                headers=first["headers"],
            ).json()["balances"]
            assert {(row["account_kind"], row["balance_minor"]) for row in balances} == {
                ("investigator", 300),
                ("party", 700),
            }


def test_inventory_offer_trade_and_recipe_commit_as_authoritative_transactions() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        app = create_app(
            Settings(
                db_path=Path(tmpdir) / "inventory-transactions.sqlite3",
                admin_token="inventory-transactions-admin",
                local_admin_enabled=False,
            )
        )
        with TestClient(app) as client:
            admin_headers = {"X-AI-KP-Admin-Token": "inventory-transactions-admin"}
            campaign = client.post(
                "/campaigns",
                headers=admin_headers,
                json={"title": "物品交易验收", "system": "coc7"},
            ).json()
            session = client.post(
                f"/campaigns/{campaign['id']}/sessions",
                headers=admin_headers,
                json={"kp_display_name": "交易 KP"},
            ).json()
            kp_headers = bearer(session["access_token"])
            first = create_approved_player_sync(
                client,
                campaign=campaign,
                session=session,
                kp_headers=kp_headers,
                display_name="制作玩家",
                sheet=coc7_sheet("制作"),
            )
            second = create_approved_player_sync(
                client,
                campaign=campaign,
                session=session,
                kp_headers=kp_headers,
                display_name="接收玩家",
                sheet=coc7_sheet("接收"),
            )
            confirm_current_session_zero_sync(
                client,
                campaign_id=campaign["id"],
                member_headers=(kp_headers, first["headers"], second["headers"]),
            )

            herbs = client.post(
                f"/campaigns/{campaign['id']}/inventory/items",
                headers=kp_headers,
                json={
                    "command_id": "create-herb-stack-0001",
                    "item_type": "herb",
                    "public_name": "干燥草药",
                    "quantity": 4,
                    "is_unique": False,
                    "holder_kind": "investigator",
                    "holder_id": first["investigator"]["id"],
                    "reason": "KP 按场景战利品表授予",
                },
            ).json()["item"]
            offer = client.post(
                f"/campaigns/{campaign['id']}/inventory/offers",
                headers=first["headers"],
                json={
                    "command_id": "offer-herbs-to-second",
                    "item_id": herbs["id"],
                    "expected_item_version": herbs["version"],
                    "quantity": 2,
                    "to_investigator_id": second["investigator"]["id"],
                },
            )
            assert offer.status_code == 200, offer.text
            accepted = client.post(
                f"/inventory/offers/{offer.json()['offer']['id']}/decisions",
                headers=second["headers"],
                json={
                    "command_id": "accept-herbs-from-first",
                    "expected_version": offer.json()["offer"]["version"],
                    "decision": "accept",
                },
            )
            assert accepted.status_code == 200, accepted.text
            assert accepted.json()["item"]["holder_id"] == second["investigator"]["id"]
            assert accepted.json()["item"]["quantity"] == 2

            grant = client.post(
                f"/campaigns/{campaign['id']}/currency/transfers",
                headers=kp_headers,
                json={
                    "command_id": "grant-shop-funds-0001",
                    "from_kind": None,
                    "from_id": None,
                    "to_kind": "investigator",
                    "to_id": second["investigator"]["id"],
                    "currency_code": "USD_CENTS",
                    "amount_minor": 1000,
                    "expected_to_version": None,
                    "reason": "角色初始现金",
                },
            ).json()["result"]["to"]
            vendor = client.post(
                f"/campaigns/{campaign['id']}/npcs",
                headers=kp_headers,
                json={
                    "name": "杂货商",
                    "profession": "商人",
                    "public_notes": "公开出售旅行用品。",
                    "secret_notes": "",
                },
            )
            assert vendor.status_code == 200, vendor.text
            vendor_id = vendor.json()["id"]
            vendor_item = client.post(
                f"/campaigns/{campaign['id']}/inventory/items",
                headers=kp_headers,
                json={
                    "command_id": "create-vendor-lamp-0001",
                    "item_type": "tool",
                    "public_name": "手提灯",
                    "quantity": 3,
                    "is_unique": False,
                    "holder_kind": "npc",
                    "holder_id": vendor_id,
                    "publicly_listed": True,
                    "unit_value_minor": 250,
                    "currency_code": "USD_CENTS",
                    "reason": "商店公开货单",
                },
            ).json()["item"]
            shop_view = client.get(
                f"/campaigns/{campaign['id']}/inventory", headers=second["headers"]
            ).json()
            listing = next(item for item in shop_view["items"] if item["id"] == vendor_item["id"])
            assert listing["unit_value_minor"] == 250
            assert "hidden_properties" not in listing
            purchased = client.post(
                f"/campaigns/{campaign['id']}/inventory/trades",
                headers=second["headers"],
                json={
                    "command_id": "purchase-vendor-lamp",
                    "direction": "purchase",
                    "item_id": vendor_item["id"],
                    "expected_item_version": vendor_item["version"],
                    "quantity": 1,
                    "investigator_id": second["investigator"]["id"],
                    "counterparty_kind": "vendor",
                    "counterparty_id": vendor_id,
                    "currency_code": "USD_CENTS",
                    "expected_investigator_balance_version": grant["version"],
                    "expected_counterparty_balance_version": None,
                },
            )
            assert purchased.status_code == 200, purchased.text
            assert purchased.json()["item"]["holder_id"] == second["investigator"]["id"]
            assert purchased.json()["price_minor"] == 250

            recipe = client.post(
                f"/campaigns/{campaign['id']}/inventory/recipes",
                headers=kp_headers,
                json={
                    "command_id": "create-herbal-poultice-recipe",
                    "public_name": "草药敷剂",
                    "inputs": [{"item_type": "herb", "quantity": 2}],
                    "output_template": {
                        "item_type": "consumable",
                        "public_name": "草药敷剂",
                        "quantity": 1,
                    },
                    "source_refs": [{"kind": "rule", "id": "fixture-recipe"}],
                },
            )
            assert recipe.status_code == 200, recipe.text
            crafted = client.post(
                f"/campaigns/{campaign['id']}/inventory/crafts",
                headers=second["headers"],
                json={
                    "command_id": "craft-herbal-poultice",
                    "recipe_id": recipe.json()["recipe"]["id"],
                    "investigator_id": second["investigator"]["id"],
                    "expected_recipe_version": recipe.json()["recipe"]["version"],
                },
            )
            assert crafted.status_code == 200, crafted.text
            assert crafted.json()["item"]["item_type"] == "consumable"
            state = client.get(
                f"/campaigns/{campaign['id']}/inventory",
                headers=second["headers"],
            ).json()
            assert {row["public_name"] for row in state["items"]} == {
                "手提灯",
                "草药敷剂",
            }
            balances = {
                (row["account_kind"], row["account_id"]): row["balance_minor"]
                for row in state["balances"]
            }
            assert balances[("investigator", second["investigator"]["id"])] == 750
            kp_balances = {
                (row["account_kind"], row["account_id"]): row["balance_minor"]
                for row in client.get(
                    f"/campaigns/{campaign['id']}/inventory", headers=kp_headers
                ).json()["balances"]
            }
            assert kp_balances[("vendor", vendor_id)] == 250


def test_approved_character_assets_materialize_once_into_the_authoritative_ledger() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "initial-assets.sqlite3"
        settings = Settings(
            db_path=db_path,
            admin_token="initial-assets-admin",
            local_admin_enabled=False,
        )
        with TestClient(create_app(settings)) as client:
            admin_headers = {"X-AI-KP-Admin-Token": "initial-assets-admin"}
            campaign = client.post(
                "/campaigns",
                headers=admin_headers,
                json={"title": "初始资产验收", "system": "coc7"},
            ).json()
            session = client.post(
                f"/campaigns/{campaign['id']}/sessions",
                headers=admin_headers,
                json={"kp_display_name": "资产 KP"},
            ).json()
            kp_headers = bearer(session["access_token"])
            player = create_approved_player_sync(
                client,
                campaign=campaign,
                session=session,
                kp_headers=kp_headers,
                display_name="有装备的玩家",
                sheet=coc7_sheet(
                    "资产调查员",
                    assets={
                        "items": [
                            "记事本",
                            {
                                "name": "急救包",
                                "item_type": "tool",
                                "quantity": 2,
                                "use_effect": {"kind": "first_aid_supply"},
                            },
                        ],
                        "currency_accounts": [
                            {"currency_code": "USD_CENTS", "amount_minor": 725}
                        ],
                    },
                ),
            )
            state = client.get(
                f"/campaigns/{campaign['id']}/inventory", headers=player["headers"]
            ).json()
            assert {(item["public_name"], item["quantity"]) for item in state["items"]} == {
                ("记事本", 1),
                ("急救包", 2),
            }
            assert state["balances"][0]["balance_minor"] == 725
            # Re-reading the approved investigator never duplicates asset materialization.
            repeated = client.get(
                f"/campaigns/{campaign['id']}/inventory", headers=player["headers"]
            ).json()
            assert len(repeated["items"]) == 2
            assert repeated["balances"][0]["balance_minor"] == 725
        with TestClient(create_app(settings)) as restarted:
            restored = restarted.get(
                f"/campaigns/{campaign['id']}/inventory", headers=player["headers"]
            )
            assert restored.status_code == 200, restored.text
            assert len(restored.json()["items"]) == 2
            assert restored.json()["balances"][0]["balance_minor"] == 725


def test_two_real_connections_cannot_pick_up_the_same_unique_loot() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        settings = Settings(
            db_path=Path(tmpdir) / "inventory-race.sqlite3",
            admin_token="inventory-race-admin",
            local_admin_enabled=False,
        )
        with TestClient(create_app(settings)) as setup:
            admin_headers = {"X-AI-KP-Admin-Token": "inventory-race-admin"}
            campaign = setup.post(
                "/campaigns",
                headers=admin_headers,
                json={"title": "唯一物品并发验收", "system": "coc7"},
            ).json()
            session = setup.post(
                f"/campaigns/{campaign['id']}/sessions",
                headers=admin_headers,
                json={"kp_display_name": "并发 KP"},
            ).json()
            kp_headers = bearer(session["access_token"])
            first = create_approved_player_sync(
                setup,
                campaign=campaign,
                session=session,
                kp_headers=kp_headers,
                display_name="并发甲",
                sheet=coc7_sheet("并发甲"),
            )
            second = create_approved_player_sync(
                setup,
                campaign=campaign,
                session=session,
                kp_headers=kp_headers,
                display_name="并发乙",
                sheet=coc7_sheet("并发乙"),
            )
            confirm_current_session_zero_sync(
                setup,
                campaign_id=campaign["id"],
                member_headers=(kp_headers, first["headers"], second["headers"]),
            )
            item = setup.post(
                f"/campaigns/{campaign['id']}/inventory/items",
                headers=kp_headers,
                json={
                    "command_id": "create-race-unique-item",
                    "item_type": "quest",
                    "public_name": "唯一封印石",
                    "quantity": 1,
                    "is_unique": True,
                    "holder_kind": "loot",
                    "holder_id": "race-loot",
                    "reason": "并发验收战利品",
                },
            ).json()["item"]
        payloads = (
            (first["headers"], "pickup-race-first"),
            (second["headers"], "pickup-race-second"),
        )
        with (
            TestClient(create_app(settings)) as first_client,
            TestClient(create_app(settings)) as second_client,
            ThreadPoolExecutor(max_workers=2) as executor,
        ):
            futures = [
                executor.submit(
                    client.post,
                    f"/inventory/items/{item['id']}/commands",
                    headers=headers,
                    json={
                        "command_id": command_id,
                        "command_type": "pickup",
                        "expected_version": item["version"],
                    },
                )
                for client, (headers, command_id) in zip(
                    (first_client, second_client), payloads, strict=True
                )
            ]
            responses = [future.result() for future in futures]
        assert sorted(response.status_code for response in responses) == [200, 409]
