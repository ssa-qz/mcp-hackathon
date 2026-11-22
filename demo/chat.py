"""Demo chatbot that knows nothing about the MCP server it talks to.

The agent gets a generic system prompt. Everything it knows about the server comes from the
server itself, discovered at startup:

  1. Connect to the server over HTTP (receiving its name and instructions in the handshake).
  2. Ask it for its tools, resources, and prompts (names, descriptions, JSON schemas).
  3. Give the model those tools, plus four generic tools for browsing resources and prompts.
  4. Chat. Each time the model calls a tool, the call is forwarded to the server.

Run it (after starting the server with `python main.py --transport http --port 8080`):

    python demo/chat.py
"""

import argparse
import asyncio
import json
import os
from pathlib import Path

import httpx
from dotenv import load_dotenv
from fastmcp import Client
from langchain.agents import create_agent
from langchain_core.messages import AIMessage
from langchain_core.tools import StructuredTool, ToolException
from langchain_openai import AzureChatOpenAI
from rich.console import Console
from rich.markdown import Markdown

# Deliberately generic: the model is not told what server it is connected to.
SYSTEM_PROMPT = "You are a helpful assistant."
DEFAULT_MCP_URL = "http://127.0.0.1:8080/mcp"
AZURE_SETTINGS = (
    "AZURE_OPENAI_ENDPOINT",
    "AZURE_OPENAI_API_KEY",
    "AZURE_OPENAI_API_VERSION",
    "AZURE_OPENAI_DEPLOYMENT_NAME",
)
EXIT_WORDS = {"exit", "quit", "bye", "stop", "end"}
NO_ARGUMENTS = {"type": "object", "properties": {}}


# --------------------------------------------------------------------------- language model
def create_llm(sync_http: httpx.Client, async_http: httpx.AsyncClient) -> AzureChatOpenAI:
    """Azure OpenAI chat model at temperature 0, so demo answers are repeatable."""
    return AzureChatOpenAI(
        azure_endpoint=os.environ["AZURE_OPENAI_ENDPOINT"],
        api_key=os.environ["AZURE_OPENAI_API_KEY"],
        api_version=os.environ["AZURE_OPENAI_API_VERSION"],
        azure_deployment=os.environ["AZURE_OPENAI_DEPLOYMENT_NAME"],
        http_client=sync_http,
        http_async_client=async_http,
        temperature=0,
    )


# --------------------------------------------------------------------------- tools
def as_tool(name: str, description: str, schema: dict, call) -> StructuredTool:
    """Wrap an async function as a tool the model can call.

    `schema` is the JSON Schema the model sees for the arguments. Tool errors are returned
    to the model as text instead of stopping the conversation.
    """
    return StructuredTool(
        name=name,
        description=description,
        args_schema=schema,
        coroutine=call,
        handle_tool_error=True,
    )


def server_tool(client: Client, tool) -> StructuredTool:
    """Turn one tool the server published into a model tool (same name, description, schema)."""

    async def call(**arguments):
        result = await client.call_tool(tool.name, arguments, raise_on_error=False)
        text = " ".join(getattr(block, "text", "") for block in result.content)
        if result.is_error:
            raise ToolException(text or "The tool reported an error.")
        return result.structured_content or text

    return as_tool(tool.name, tool.description or tool.name, tool.input_schema, call)


def browsing_tools(client: Client) -> list[StructuredTool]:
    """Four generic tools for resources and prompts. They work for any MCP server."""

    async def list_resources() -> str:
        resources = await client.list_resources()
        templates = await client.list_resource_templates()
        return json.dumps({
            "resources": [{"uri": str(r.uri), "name": r.name, "description": r.description} for r in resources],
            "resource_templates": [
                {"uri_template": t.uri_template, "name": t.name, "description": t.description} for t in templates
            ],
        }, indent=2)

    async def read_resource(uri: str) -> str:
        contents = await client.read_resource(uri)
        return "\n".join(getattr(item, "text", "") for item in contents)

    async def list_prompts() -> str:
        prompts = await client.list_prompts()
        return json.dumps({"prompts": [
            {"name": p.name, "description": p.description, "arguments": [a.name for a in (p.arguments or [])]}
            for p in prompts
        ]}, indent=2)

    async def get_prompt(name: str, arguments: dict | None = None) -> str:
        result = await client.get_prompt(name, arguments or {})
        return "\n".join(m.content.text for m in result.messages if hasattr(m.content, "text"))

    return [
        as_tool("list_resources", "List the readable resources and resource templates.",
                NO_ARGUMENTS, list_resources),
        as_tool("read_resource", "Read the content of a resource by its URI.",
                {"type": "object", "properties": {"uri": {"type": "string"}}, "required": ["uri"]},
                read_resource),
        as_tool("list_prompts", "List the prompt templates.", NO_ARGUMENTS, list_prompts),
        as_tool("get_prompt", "Fill in a prompt template by name and return its messages.",
                {"type": "object", "properties": {
                    "name": {"type": "string"},
                    "arguments": {"type": "object", "additionalProperties": {"type": "string"}},
                }, "required": ["name"]},
                get_prompt),
    ]


# --------------------------------------------------------------------------- discovery
async def discover(client: Client) -> tuple[list[StructuredTool], dict[str, int]]:
    """Ask the server what it offers and build the model's tool list from the answers."""
    tools = await client.list_tools()
    resources = await client.list_resources()
    templates = await client.list_resource_templates()
    prompts = await client.list_prompts()
    counts = {"tools": len(tools), "resources": len(resources),
              "resource templates": len(templates), "prompts": len(prompts)}
    model_tools = [server_tool(client, tool) for tool in tools] + browsing_tools(client)
    return model_tools, counts


# --------------------------------------------------------------------------- conversation
console = Console()


def show_tool_calls(new_messages: list) -> None:
    """Print each tool call the model made, so the audience can follow its reasoning."""
    for message in new_messages:
        if isinstance(message, AIMessage):
            for call in message.tool_calls or []:
                print(f"  [tool call]   {call['name']}({call['args']})")


def show_answer(content: object) -> None:
    """Render Markdown responses (such as tables) cleanly in the terminal."""
    print("\nAssistant:")
    console.print(Markdown(str(content)))


async def chat(agent) -> None:
    """Read questions in a loop and answer each one. The history carries over between turns."""
    history: list = []
    print("\nAsk a question. Type 'exit' to quit.")
    while True:
        try:
            question = (await asyncio.to_thread(input, "\nYou: ")).strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if not question:
            continue
        if question.lower() in EXIT_WORDS:
            break

        history.append({"role": "user", "content": question})
        messages = (await agent.ainvoke({"messages": history}))["messages"]
        show_tool_calls(messages[len(history):])
        history = messages
        show_answer(messages[-1].content)


# --------------------------------------------------------------------------- main
async def run(mcp_url: str) -> None:
    # Keep one connection open for the whole chat; every tool call reuses it.
    async with Client(mcp_url) as client, httpx.AsyncClient(trust_env=True, timeout=120) as async_http:
        with httpx.Client(trust_env=True, timeout=120) as sync_http:
            model_tools, counts = await discover(client)
            summary = ", ".join(f"{count} {name}" for name, count in counts.items())
            server_name = (
                client.server_info.name
                if client.server_info and client.server_info.name
                else mcp_url
            )
            extra = ", server instructions" if client.instructions else ""
            print(f"Connected to {server_name} ({mcp_url}): {summary}{extra} discovered.")

            system_prompt = (
                f"{SYSTEM_PROMPT}\n\n{client.instructions}"
                if client.instructions
                else SYSTEM_PROMPT
            )
            agent = create_agent(
                model=create_llm(sync_http, async_http),
                tools=model_tools,
                system_prompt=system_prompt,
            )
            await chat(agent)


def main() -> None:
    parser = argparse.ArgumentParser(description="Chat with an MCP server without telling the model about it.")
    parser.add_argument("--env-file", type=Path, default=Path(__file__).resolve().parents[1] / ".env",
                        help="dotenv file containing the AZURE_OPENAI_* settings")
    parser.add_argument("--mcp-url", default=os.getenv("DEMO_MCP_URL", DEFAULT_MCP_URL),
                        help="streamable-HTTP URL of the MCP server")
    args = parser.parse_args()

    load_dotenv(args.env_file)
    missing = [name for name in AZURE_SETTINGS if not os.getenv(name)]
    if missing:
        raise SystemExit(f"Missing Azure OpenAI settings: {', '.join(missing)} (see --env-file)")
    asyncio.run(run(args.mcp_url))


if __name__ == "__main__":
    main()
