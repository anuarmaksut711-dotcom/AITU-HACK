"""Provider schemas require every property, including nullable optional fields."""


def strict_response_schema(model):
    # Keep defaults in application models for older persisted responses, but not on the wire.
    schema = model.model_json_schema()

    def visit(node):
        if isinstance(node, list):
            for child in node:
                visit(child)
        elif isinstance(node, dict):
            node.pop("default", None)
            if node.get("type") == "object" or "properties" in node:
                node["additionalProperties"] = False
                node["required"] = list(node.get("properties", {}))
            for child in node.values():
                visit(child)

    visit(schema)
    return schema
