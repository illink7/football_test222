"""Run the FastAPI Web App."""
import uvicorn
uvicorn.run("webapp.main:app", host="0.0.0.0", port=8000, reload=False)
