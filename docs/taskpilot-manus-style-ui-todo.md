# TaskPilot Product UI Redesign ToDo

## Goal

Turn the current debug-oriented TaskPilot console into an Agent workspace for regular users.

Use Manus as a product reference, but do not copy its branding or exact interactions. The key ideas to learn from are its page organization:

- A stable left-side navigation.
- A clear central entry point for creating new tasks.
- Frequently used capabilities exposed as shortcut actions.
- Dedicated entry points for task history, projects, Agents, plugins, schedules, and library.
- Execution process and final artifacts can be reviewed later.

## Completion Criteria

- The first screen looks like a usable workspace, not a debugging page.
- Users can clearly create tasks, choose Agents, upload files, select tools, and review task history.
- Existing task capabilities must remain: task creation, running status, process replay, tool calls, artifacts, retry, cancel, and follow-up input.
- Debug options are not exposed by default. Run mode, output format, and tool details belong in advanced settings.
- Every new entry point should be backed by real data first. If the backend does not yet fully support it, show a clear empty state and document the follow-up API plan.
- After each stage, open the page and check both desktop and mobile widths. At minimum, run `tests/tasks/test_autoagent_web.py` and the task control tests.

## Current Problems

- The home page has low product density and feels like an engineering console instead of a finished product.
- The sidebar only contains conversations and recent tasks. It lacks clear product-level sections.
- Many existing capabilities are hidden: Agent selection, tool selection, task filtering, evals, file upload, and advanced runtime settings.
- Task detail does not clearly separate process, result, and artifacts.
- Agents, plugins, projects, library, and scheduled tasks do not have dedicated pages.
- Browser tasks, file tasks, report tasks, data analysis, and similar capabilities are not exposed as shortcut entries.
- Empty states are mostly explanatory and do not guide users toward starting work.

## Redesign Principles

- Improve information architecture before visual details.
- Expose existing capabilities first, then fill backend gaps.
- The default UI is for regular users; advanced settings are collapsed.
- Tasks are the main thread: every Agent, tool, file, project, and artifact should link back to task records.
- Keep the visual style restrained: light gray sidebar, white main area, thin borders, subtle shadows, limited accent colors, border radius within 8px, icons plus short labels.
- Do not build a marketing landing page. Opening the page should show the workspace.

## ToDo List

### 1. Rebuild The Overall Page Shell

Status: Not started

What to change:

- Turn the existing `autoagent.html` from a chat page into a workspace shell.
- Fix the left navigation as: New Task, Agents, Plugins, Scheduled Tasks, Library, Projects, All Tasks.
- Keep low-frequency entries such as settings, layout switching, and source attribution at the bottom of the sidebar.
- Keep the current model or default Agent, notifications, and user entry in the top area.

How to change:

- Modify the HTML structure in `task-pilot-agent/brain/web/autoagent.html`.
- Add an `activeView` state so one page can host multiple views: home, agents, plugins, schedules, library, projects, tasks, and taskDetail.
- Move the existing task list from the sidebar bottom into the All Tasks view. The sidebar should only keep a short recent-task entry.
- Render the left menu with one shared navigation item renderer so future entries do not require changes in multiple DOM locations.

Acceptance:

- The page opens on New Task by default.
- Each left navigation entry switches views.
- On mobile, the sidebar can collapse and does not cover the input area.

### 2. Rebuild The New Task Home Page

Status: Not started

What to change:

- Show a central prompt such as "What can I do for you?"
- Replace the input with a large Manus-like multiline task composer.
- Put common actions inside or below the composer: upload files, choose tools, cloud browser or browser task, voice input, and send.
- Put shortcut tasks below the composer: make slides, create website, data analysis, browser task, file processing, write report, code task, more.

How to change:

- Replace the current fixed bottom input with the main home composer. Keep a follow-up input on the task detail page.
- Clicking a shortcut should fill the matching prompt or select the matching Agent.
- Show the file upload button by default instead of hiding it.
- Keep the Agent dropdown, but place it near the composer as the "who should do this" choice.
- Move output format, run mode, and runtime environment into an Advanced Settings drawer or popover.

Acceptance:

- New users can submit a task without opening advanced settings.
- Clicking a shortcut selects a suitable Agent or pre-fills the task.
- Uploaded file count is shown near the composer.

### 3. Establish A Unified Visual Style

Status: Not started

What to change:

- Remove the heavy gradient background and debugging-card feel.
- Use a light gray sidebar, white content area, thin borders, and subtle shadows.
- Standardize icon button sizes. Use text buttons only for clear commands.
- Standardize status tag colors: running, waiting for input, completed, failed, cancelled.

How to change:

- Rewrite design variables at the top of the CSS in `autoagent.html`: background, text, borders, status colors, spacing, and radius.
- Add shared styles for buttons, nav items, inputs, tags, list items, and empty states.
- If still using a single HTML file, prefer reusable CSS classes. Componentize later when the frontend is split into a proper app.
- Prefer an existing icon library or lightweight icon resources. Avoid large amounts of handwritten symbol markup.

Acceptance:

- The page looks like a real application, not a development console.
- Text does not overlap on desktop, tablet, or mobile widths.
- Input area, task list, and process cards have clear visual hierarchy.

### 4. Productize The All Tasks Page

Status: Not started

What to change:

- Turn the currently hidden task filtering capability into a formal page.
- Support filtering by status, keyword, user, Agent, time, duration, and error state.
- Task rows should show input summary, status, Agent, created time, duration, artifact count, and failure summary.

How to change:

- Continue using the existing `/agent/tasks` API.
- Move the filter controls from the sidebar into the All Tasks main view.
- Clicking a task should open the task detail view.
- If backend fields are not enough, read Agent snapshot, artifacts, and usage information from `metadata` first.

Acceptance:

- Filters make real backend requests and refresh the list.
- Failed tasks show a failure summary directly.
- Clicking a task replays its full historical process.

### 5. Split Task Detail Into Two Or Three Areas

Status: Not started

What to change:

- Replace the current message stream with a task-detail style page.
- The detail page should include: task input, current status, current Agent, plan, timeline, tool calls, tool results, final answer, errors, and artifacts.
- Artifacts should have their own area with download or preview.

How to change:

- Continue using `/agent/tasks/{task_id}`, `/events`, and `/artifacts`.
- Show the process timeline in the main area or left area.
- Show task summary and artifact list on the right.
- Put the final answer in a prominent location near the top or bottom of the detail page, not buried inside process cards.
- Tool calls are collapsed by default, but failures and risk events open by default.

Acceptance:

- Users can immediately tell whether the task is complete, what the result is, and where artifacts are.
- Tool calls and errors are not hidden.
- Historical tasks still render fully after page refresh.

### 6. Agents Page

Status: Not started

What to change:

- Add a dedicated Agents page that lists all available Agents.
- Each Agent should show name, purpose, capability tags, available tools, permissions, and allowed handoffs.
- Users can start a task directly from an Agent page.

How to change:

- Use the existing `/agent/agents`, `/agent/agents/{agent_id}`, and `/agent/tools?agent_id=...` APIs.
- Group Agent cards by type: supervisor, search, browser, data, code, report.
- Show high-risk tools clearly and do not select them by default.
- Agent configuration errors should appear on the page instead of only inside debugging summaries.

Acceptance:

- Users can understand what each Agent is good for.
- Users can start a new task from an Agent card with that Agent preselected.
- Configuration errors are visible and can be traced to specific Agents.

### 7. Plugins Page

Status: Not started

What to change:

- Add a Plugins page that shows available and unavailable tools.
- Distinguish built-in tools, MCP tools, and external extension tools.
- Show purpose, input and output summary, risk level, availability, and unavailable reason.

How to change:

- Reuse the `/agent/tools` API in the short term.
- If an Agent is selected, show tools available to that Agent.
- Later, add plugin management APIs: enable, disable, configure secrets, and test connection.
- Use clear toggles and approval explanations for high-risk capabilities such as shell, code execution, and file write.

Acceptance:

- Users know what tools the system can call.
- Unavailable tools have reasons instead of simply disappearing.
- High-risk tools are not enabled accidentally.

### 8. Projects Page

Status: Not started

What to change:

- Add a Projects entry to organize a group of tasks, files, default instructions, and default Agent.
- Projects support long-running work such as research topics, customer reports, code projects, and data analysis projects.

How to change:

- In the first stage, this can be a frontend empty state and a project creation form.
- Backend should add a project table with: project ID, name, description, default Agent, default tools, default output format, file scope, owner, and timestamps.
- Task creation should accept `project_id` and write it into task `metadata`.
- Task list should support filtering by project.

Acceptance:

- Users can create projects and start tasks inside projects.
- Project detail shows tasks under that project.
- Project defaults affect new tasks.

### 9. Library Page

Status: Not started

What to change:

- Add a Library entry to manage uploaded files, knowledge materials, and generated artifacts.
- Users can select files from the library and attach them to a new task.

How to change:

- In the short term, connect to existing file upload, preview, and download APIs.
- Page sections: upload files, recent files, task artifacts, knowledge materials.
- Later, add file ownership, tags, project links, and retrieval status.
- When a file is used by a task, the task detail page should show the source file.

Acceptance:

- Users can upload files and see them on the page.
- Files can be used as new task input.
- Task artifacts can be managed from the library.

### 10. Scheduled Tasks Page

Status: Not started

What to change:

- Add a Scheduled Tasks entry.
- Support one-time, daily, weekly, and monthly runs.
- Show run history and the latest result.

How to change:

- Add a backend scheduled task model: title, task input, Agent, tools, schedule rule, status, last run, next run, owner.
- Implement page structure and empty state first, then connect the real create API.
- Every scheduled run should create a normal task record instead of using a separate result store.
- Scheduled task detail should link to historical task records.

Acceptance:

- Users can create, pause, resume, and delete scheduled tasks.
- Every run has a normal task record that can be reviewed.
- Failures show clear reasons.

### 11. Browser And Runtime Visualization

Status: Not started

What to change:

- Turn "browser task", "local runtime", and "sandbox runtime" from hidden config into understandable user options.
- Browser tasks should show a clear notice: they may visit web pages, take screenshots, extract page content, and require confirmation for sensitive actions.

How to change:

- Add Browser Task to the new task shortcuts.
- Selecting Browser Task should default to `browser_agent`.
- In task detail, use dedicated icons and summaries for browser-related tool calls.
- If a visual browser panel is added later, place it in the artifact area on the right or in a dedicated runtime area.

Acceptance:

- Users can explicitly choose "let the Agent operate a web page".
- Actions involving login, submission, deletion, purchase, or similar impact must stop and wait for user confirmation.
- Browser-related results are clear in task detail.

### 12. Artifact Preview And Finished Output Entry

Status: Not started

What to change:

- Treat reports, PPTs, HTML, images, and tables as artifacts, not only as chat replies.
- Home shortcuts should guide users toward generating these finished outputs.

How to change:

- Add an artifact list and preview area to the right side of task detail.
- Support online preview first for HTML, Markdown, images, and tables.
- Provide download for PPT and documents first; preview can come later.
- If artifact generation fails, show the failure reason in the artifact area.

Acceptance:

- Users can find final files without reading through the process.
- Artifact count, type, and download entry are clear.
- Artifacts remain accessible after refresh.

### 13. Advanced Settings Drawer

Status: Not started

What to change:

- Move run mode, output format, runtime environment, tool selection, and eval entry into advanced settings.
- By default, new task creation only exposes Agent, files, shortcuts, and send.

How to change:

- Add an Advanced Settings button.
- Clicking it opens a drawer or popover.
- Move existing hidden controls into advanced settings instead of scattering them around the input bar.
- Show eval entry only in development or admin mode.

Acceptance:

- Regular users are not distracted by Plans Executor, ReAct, eval, or similar settings.
- Advanced users can still select tools and runtime options.
- Existing tests for default submission behavior still pass.

### 14. Notifications And Task Completion Reminders

Status: Not started

What to change:

- Add a notification entry in the top area.
- Support reminders for task completion, failure, and waiting-for-input states.

How to change:

- In the first stage, implement a frontend notification center based on current session and recent task status.
- Later, connect browser notifications and server-side events.
- If users allow notifications, send browser notifications when a task completes.

Acceptance:

- Running task completion is clearly visible.
- Waiting-for-input tasks are not easy to miss.
- Users can close or clear notifications.

### 15. Mobile Adaptation

Status: Not started

What to change:

- Mobile should not be only a compressed desktop page.
- The left navigation becomes a drawer, and task input gets priority.
- Task detail uses single-column switching: overview, process, artifacts.

How to change:

- Use CSS media queries to rewrite layouts below 900px and 640px.
- Fixed-format controls should have stable dimensions so they do not jump after interactions.
- Long task titles, tool names, and file names must truncate or wrap cleanly.

Acceptance:

- Users can create tasks, review tasks, and open artifacts on mobile widths.
- No text squeezing, button overlap, or uncontrolled horizontal scrolling.

### 16. Tests And Acceptance Scripts

Status: Not started

What to change:

- Every page change needs automated checks.
- Visual and interaction behavior should be verified by opening the page in a browser.

How to change:

- Update `tests/tasks/test_autoagent_web.py` to cover new navigation, new task creation, hidden advanced settings, and task replay.
- Keep and update task control tests so cancel, retry, follow-up input, and task listing remain unaffected.
- After page changes, start the local service and open `/agent/web/autoagent` for desktop and mobile width checks.

Acceptance:

- `uv run pytest tests/tasks/test_autoagent_web.py tests/tasks/test_task_control_api.py -q` passes.
- The local page opens and the main flow can be clicked through.
- If some backend capabilities are not yet implemented, the page must clearly show "not connected yet" and must not pretend the feature works.

## Recommended Execution Order

1. Build the page shell, navigation, and home task composer first.
2. Then build the All Tasks page and task detail page.
3. Then build the Agents page and Plugins page, because they can reuse existing APIs directly.
4. Then build Projects, Library, and Scheduled Tasks; these three areas need backend models and APIs.
5. Finally add notifications, browser visualization, artifact preview improvements, and mobile details.

## First-Stage Minimum Delivery Scope

The first stage should only change the frontend page and avoid adding complex backend models:

- New navigation.
- New task home page.
- All Tasks view.
- Task Detail view.
- Agents view.
- Plugins view.
- Advanced Settings drawer.

Projects, Library, and Scheduled Tasks can temporarily be empty-state pages with clear "to be connected later" messaging.

## First-Stage Files

- `task-pilot-agent/brain/web/autoagent.html`
  - Main page structure, styling, and interaction logic.

- `task-pilot-agent/tests/tasks/test_autoagent_web.py`
  - Page structure, default submission, hidden advanced settings, navigation entries, and task replay tests.

- `task-pilot-agent/tests/tasks/test_task_control_api.py`
  - Confirm task creation, cancel, retry, follow-up input, task list, and event filtering still work.

## Possible Second-Stage Files

- `task-pilot-agent/brain/app.py`
  - Add APIs for projects, library, and scheduled tasks.

- `task-pilot-agent/brain/core/tasks.py`
  - Link tasks to projects, register artifacts in the library, and record scheduled task runs.

- `task-pilot-agent/file/file_op.py`
  - File library display, file ownership, and project links.

- `config/agents/*/agent.yaml`
  - Complete default Agent capabilities and default tool descriptions for shortcut tasks.

## Risks And Notes

- Do not break existing task replay. It is currently one of the most valuable product foundations.
- Do not hide tool calls, errors, or risk warnings. They can only be shown in a clearer way.
- Projects, Library, and Scheduled Tasks should not remain frontend-only fake pages. They must connect to real task records as soon as possible.
- High-risk tools must not be enabled by default because of productization.
- The visual style can reference Manus's clean workspace feel, but do not copy its brand, copy, or graphic assets.
