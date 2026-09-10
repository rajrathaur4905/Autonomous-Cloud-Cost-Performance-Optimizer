from dotenv import load_dotenv
import os
load_dotenv()
AZURE_SUBSCRIPTION_ID = os.getenv("AZURE_SUBSCRIPTION_ID")
AZURE_VM_RESOURCE_ID = os.getenv("AZURE_VM_RESOURCE_ID")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
LLM_MODEL = os.getenv("LLM_MODEL", "gpt-4o-mini")
DB_PATH = os.path.join(os.path.dirname(__file__), "..", "data", "metrics.db")