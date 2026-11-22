import pytest

from ssa_server.core.baby_name_data import (
    query_name_popularity,
    query_top_names,
    query_name_trend,
    compare_names,
    get_all_time_leaders,
    get_dataset_metadata,
)


def test_query_name_popularity_mary():
    res = query_name_popularity("Mary", "F", 1880, 1885)
    assert res["found"] is True
    assert res["name"] == "Mary"
    assert res["sex"] == "F"
    assert len(res["history"]) == 6
    assert res["history"][0]["year"] == 1880
    assert res["history"][0]["rank"] == 1
    assert res["best_rank"]["rank"] == 1


def test_query_name_case_insensitive():
    res_lower = query_name_popularity("john", "M", 1900, 1905)
    res_upper = query_name_popularity("JOHN", "M", 1900, 1905)
    assert res_lower["found"] is True
    assert res_lower["total_births_in_range"] == res_upper["total_births_in_range"]
    assert res_lower["peak_year"] == res_upper["peak_year"]


def test_query_name_not_found():
    res = query_name_popularity("NonexistentName999", "F", 2000, 2025)
    assert res["found"] is False
    assert res["total_births"] == 0
    assert "No records found" in res["message"]


def test_invalid_parameters():
    with pytest.raises(ValueError, match="sex"):
        query_name_popularity("Mary", "X")  # type: ignore

    with pytest.raises(ValueError, match="Year range"):
        query_name_popularity("Mary", "F", 1850, 1900)

    with pytest.raises(ValueError, match="start_year"):
        query_name_popularity("Mary", "F", 2020, 2010)


def test_query_top_names_1880():
    res = query_top_names(1880, "F", top_n=3)
    assert res["year"] == 1880
    assert res["sex"] == "F"
    assert len(res["top_names"]) == 3
    assert res["top_names"][0]["name"] == "Mary"
    assert res["top_names"][0]["rank"] == 1
    assert res["top_names"][1]["name"] == "Anna"
    assert res["top_names"][2]["name"] == "Emma"


def test_query_top_names_2025():
    res_f = query_top_names(2025, "F", top_n=1)
    res_m = query_top_names(2025, "M", top_n=1)
    assert res_f["top_names"][0]["name"] == "Olivia"
    assert res_m["top_names"][0]["name"] == "Liam"


def test_query_name_trend():
    trend = query_name_trend("Liam", "M")
    assert trend["found"] is True
    assert trend["total_lifetime_births"] > 300000
    assert any(d["decade"] == "2020s" for d in trend["decades"])
    # In recent decades Liam is rank 1
    dec_2020 = next(d for d in trend["decades"] if d["decade"] == "2020s")
    assert dec_2020["best_rank"] == 1


def test_compare_names():
    comp = compare_names(["Emma", "Olivia"], "F", 2015, 2025)
    assert "Emma" in comp["metrics"]
    assert "Olivia" in comp["metrics"]
    assert comp["metrics"]["Emma"]["total_births"] > 0
    assert comp["metrics"]["Olivia"]["total_births"] > 0


def test_all_time_leaders():
    leaders = get_all_time_leaders("M", top_n=3)
    names = [x["name"] for x in leaders["leaders"]]
    assert "James" in names
    assert "John" in names
    assert "Robert" in names


def test_dataset_metadata():
    meta = get_dataset_metadata()
    assert meta["years_covered"] == "1880 - 2025"
    assert meta["total_records"] == 2181032
    assert meta["unique_given_names"] > 100000



def test_baby_name_mcp_discovery_metadata():
    import asyncio
    from fastmcp import Client
    from ssa_server.app import mcp

    baby_tools = {
        "get_baby_name_popularity", "get_top_names_by_year", "get_name_trend",
        "compare_names", "get_all_time_popular_names",
    }

    async def check():
        async with Client(mcp) as client:
            tools = {tool.name: tool for tool in await client.list_tools()}
            assert baby_tools <= set(tools)
            for name in baby_tools:
                annotations = tools[name].annotations.model_dump(by_alias=True)
                assert annotations["readOnlyHint"] is True
                assert annotations["idempotentHint"] is True
                assert annotations["openWorldHint"] is False
            resources = {str(resource.uri): resource for resource in await client.list_resources()}
            assert resources["ssa://datasets/baby-names/metadata"].mime_type == "application/json"
            assert resources["ssa://datasets/baby-names/all-time-top-10"].mime_type == "text/markdown"
            table = await client.read_resource("ssa://datasets/baby-names/all-time-top-10")
            assert "# SSA All-Time Top 10 Names" in table[0].text
            prompt = await client.get_prompt("demographic_name_analysis", {"name": "Mary", "sex": "F"})
            assert "ssa://datasets/baby-names/metadata" in prompt.messages[0].content.text
            result = await client.call_tool("get_top_names_by_year", {"year": 1880, "sex": "F", "top_n": 1})
            assert result.structured_content["top_names"][0]["name"] == "Mary"

    asyncio.run(check())
