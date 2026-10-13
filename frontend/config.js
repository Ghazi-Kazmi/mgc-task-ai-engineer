// Where the API lives.
//   ""                            -> same origin (local FastAPI, or Vercel proxying /api/* via vercel.json)
//   "https://xyz.up.railway.app"  -> call the backend directly (then set CORS_ORIGINS on the backend)
window.MGC_API_BASE = "";

// Link shown in the footer (leave empty to hide it).
window.MGC_REPO_URL = "";
