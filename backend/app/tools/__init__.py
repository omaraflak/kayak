from backend.app.tools.builtins.command_tools import run_command
from backend.app.tools.builtins.file_tools import (
    edit_file,
    find_files,
    list_directory,
    read_file,
    write_file,
)
from backend.app.tools.builtins.memory_tools import remember
from backend.app.tools.builtins.python_tools import run_python
from backend.app.tools.builtins.skill_management_tools import (
    create_or_update_skill,
    list_available_skills,
)
from backend.app.tools.builtins.skill_tools import load_skill
from backend.app.tools.builtins.subagent_tools import (
    get_subagent_result,
    spawn_subagent,
)
from backend.app.tools.builtins.task_tools import (
    get_task_status,
    send_task_input,
    start_background_task,
    stop_task,
)
from backend.app.tools.builtins.tool_management_tools import (
    activate_tool,
    get_tool_source,
    verify_tool,
)
from backend.app.tools.builtins.web_tools import fetch_url, web_search
from backend.app.tools.registry import tool_registry

# Register all core built-ins
tool_registry.register_builtin(read_file)
tool_registry.register_builtin(write_file)
tool_registry.register_builtin(edit_file)
tool_registry.register_builtin(list_directory)
tool_registry.register_builtin(find_files)
tool_registry.register_builtin(run_command)
tool_registry.register_builtin(run_python)
tool_registry.register_builtin(start_background_task)
tool_registry.register_builtin(get_task_status)
tool_registry.register_builtin(send_task_input)
tool_registry.register_builtin(stop_task)
tool_registry.register_builtin(spawn_subagent)
tool_registry.register_builtin(get_subagent_result)
tool_registry.register_builtin(web_search)
tool_registry.register_builtin(fetch_url)
tool_registry.register_builtin(load_skill)
tool_registry.register_builtin(remember)
tool_registry.register_builtin(verify_tool)
tool_registry.register_builtin(activate_tool)
tool_registry.register_builtin(get_tool_source)
tool_registry.register_builtin(create_or_update_skill)
tool_registry.register_builtin(list_available_skills)

# Initial load of custom tools from data/tools/
tool_registry.load_custom_tools()
