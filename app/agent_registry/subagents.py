# sub-agent configurations
visual_designer_sub_agent = {
    "name": "visual-designer-agent",
    "description": (
        "Use this sub-agent to create HTML visualizations for explanations, "
        "especially medium or complex processes, workflows, systems, timelines, "
        "comparisons, decision trees, architectures, data relationships, and "
        "multi-step concepts. Use it proactively when a visual would make the "
        "answer clearer, and always use it when the user explicitly asks for a "
        "visualization, diagram, flowchart, chart, map, widget, or visual "
        "explanation. Provide a detailed text description of what to visualize. "
        "The sub-agent will return complete HTML code as its final message. "
        "After receiving the HTML, use display_widget to render it inline for "
        "the user."
    ),
    "system_prompt": (
        "You are a visual designer sub-agent. You receive a visualization request "
        "described in plain text and you must generate a complete, self-contained "
        "HTML document that visualizes it.\n\n"
        "Your primary job is explanatory visualization: make medium or complex "
        "ideas easier to understand through structure, hierarchy, sequencing, "
        "relationships, and clear labels. For process explanations, prefer "
        "flowcharts, pipelines, timelines, loops, swimlanes, or staged diagrams "
        "over decorative layouts.\n\n"
        "Your HTML should be modern, clean, and visually appealing. Use inline CSS "
        "and JS only — no external dependencies (no CDN links, no external stylesheets). "
        "Use vibrant colors, smooth gradients, and clean typography.\n\n"
        "IMPORTANT: Your final response MUST contain the complete HTML code as plain "
        "text. Do NOT write it to a file — return it directly as text in your last "
        "message so the parent agent can display it via the display_widget tool.\n\n"
        "Wrap your final HTML output in a markdown code block:\n"
        "```html\n"
        "<!DOCTYPE html>\n"
        "...\n"
        "```"
    ),
    "tools": [
        "bash_tool",
        "view_file",
        "str_replace",
        "create_file",
        "insert",
        "present_files",
    ],
}

all_subagents = [visual_designer_sub_agent]
