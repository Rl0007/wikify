app_name = "wikify"
app_title = "Wikify"
app_publisher = "BWH"
app_description = "PDFs to Frappe Wiki Spaces/Documents"
app_email = "developers@bwh.tech"
app_license = "mit"

required_apps = ["wiki"]

# The Frappe UI SPA is mounted at /wikify and served by www/wikify.py; every deeper path
# is handed to the client-side router rather than resolved as a website route.
website_route_rules = [
	{"from_route": "/wikify/<path:app_path>", "to_route": "wikify"},
]
app_icon_route = "/wikify"

add_to_apps_screen = [
	{
		"name": "wikify",
		"logo": "/assets/wikify/images/logo.svg",
		"title": "Wikify",
		"route": "/wikify",
		"has_permission": "wikify.api.permission.has_app_permission",
	}
]

after_install = "wikify.install.after_install"

# Fetch the retrieval models on deploy rather than inside the first question — see
# `rag.warm` for what that costs on the web worker.
after_migrate = ["wikify.rag.warm.warm_models"]

# Only the ORM writes to Source Section reach these. The writes that go through
# `engine.store` use `frappe.db.set_value`, which fires no doc_event, so that path
# invalidates explicitly — see `rag.events.section_content_changed`.
doc_events = {
	"Source Section": {
		"after_insert": "wikify.rag.events.queue_reindex",
		"on_update": "wikify.rag.events.queue_reindex",
		"on_trash": "wikify.rag.events.queue_reindex",
	},
}

export_python_type_annotations = True
require_type_annotated_api_methods = True
