import importlib
import pkgutil
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from .security import ensure_superadmin_account

BASE_DIR = Path(__file__).resolve().parents[1]


def _discover_modules():
    modules_dir = BASE_DIR / "modules"
    if not modules_dir.exists():
        return []
    discovered = []
    for _, module_name, is_pkg in pkgutil.iter_modules([str(modules_dir)]):
        if not is_pkg:
            continue
        discovered.append(module_name)
    return discovered


def get_templates():
    template_dirs = [str(BASE_DIR / "core" / "templates")]
    modules_dir = BASE_DIR / "modules"
    if modules_dir.exists():
        for module in modules_dir.iterdir():
            mod_templates = module / "templates"
            if mod_templates.is_dir():
                template_dirs.append(str(mod_templates))
    return Jinja2Templates(directory=template_dirs)


templates = get_templates()


def create_app() -> FastAPI:
    app = FastAPI(title="Modular Mutasi App")

    app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")

    for module_name in _discover_modules():
        try:
            mod = importlib.import_module(f"modules.{module_name}.router")
            if hasattr(mod, "router"):
                app.include_router(mod.router)
                print(f"Loaded module: {module_name}")
        except ImportError as exc:
            print(f"Failed to load module {module_name}: {exc}")
        except Exception as exc:
            print(f"Error loading module {module_name}: {exc}")

    @app.on_event("startup")
    def _on_startup():
        ensure_superadmin_account()

    return app
