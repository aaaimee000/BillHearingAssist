#FastAPI app entry point 
from fastapi import FastAPI
from plugins.registry import REGISTRY 

app = FastAPI()

@app.get("/run/{plugin_name}")
async def run_plugin(plugin_name: str, inputs: dict):
    plugin = REGISTRY.get(plugin_name)
    if not plugin:
        return {"error": "Plugin not found"}
    result = await plugin.run(inputs)
    return result