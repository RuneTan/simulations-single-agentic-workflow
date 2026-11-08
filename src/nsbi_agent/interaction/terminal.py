"""A small terminal conversation around the existing task runner."""
from pathlib import Path
import time

from nsbi_agent.interaction.tasks import create_task, list_tasks, save_answer, save_outputs, task_path
from nsbi_agent.workflow import runner

HELP = 'Enter a request / 输入自然语言；/tasks list / 查看任务；/resume task1 resume / 继续；/quit exit / 退出。'
STATUS = {'created': 'created / 已创建', 'running': 'running / 运行中', 'waiting_job': 'job running / 后台作业运行中',
          'awaiting_user': 'awaiting answer / 等待回答', 'completed': 'completed / 已完成', 'failed': 'failed / 失败'}


def run_chat(*, tasks_root: Path | None = None, input_fn=None, output_fn=None,
             run_fn=None, resume_fn=None, sleep_fn=None) -> int:
    read = input_fn or input
    show = output_fn or print
    run = run_fn or runner.run_task
    resume = resume_fn or runner.run_resume
    sleep = sleep_fn or time.sleep
    active = None
    show('Simulations Single Agentic Workflow · 自然语言任务窗口')
    show(HELP)
    try:
        while True:
            text = read('\nYou / 你：').strip()
            if not text:
                continue
            if text in {'/quit', '/exit'}:
                break
            if text == '/help':
                show(HELP)
                continue
            if text == '/tasks':
                entries = list_tasks(tasks_root)
                show('\n'.join(f'{name}：{STATUS.get(status, status)}' for name, status in entries)
                     or 'No tasks yet / 还没有编号任务。')
                continue
            try:
                if text.startswith('/resume '):
                    active = task_path(text.split(maxsplit=1)[1].strip(), tasks_root)
                    show(f'Resuming / 继续 {active.parent.name}。')
                    result = resume(str(active))
                elif text.startswith('/'):
                    show(HELP)
                    continue
                else:
                    active = create_task(text, tasks_root)
                    show(f'Created / 已创建 {active.parent.name}：{active}')
                    show('Agent is working / Agent 正在处理请求，请稍候。')
                    result = run(str(active))
                result = _continue_task(active, result, read, show, resume, sleep)
                index = save_outputs(active, result)
                _show_result(result, show)
                if index:
                    show(f'Outputs / 输出索引：{index}')
                active = None
            except (OSError, ValueError) as error:
                show(f'Task input / 任务入口：{error}')
                if active:
                    show(f'Task file / 任务文件：{active}')
                active = None
    except (EOFError, KeyboardInterrupt):
        show('')
        if active:
            show(f'Task saved / 任务已保留。Resume / 继续：/resume {active.parent.name}')
    show('Exited / 已退出任务窗口。')
    return 0


def _continue_task(path, result, read, show, resume, sleep):
    waiting_notice = False
    while result.get('exit_code') in (3, 5):
        if result['exit_code'] == 3:
            if not waiting_notice:
                show('Job running; will resume automatically / 后台作业运行中，完成后自动继续。Ctrl+C: leave and resume later / 退出后可恢复。')
                waiting_notice = True
            sleep(2)
        else:
            questions = result.get('user_questions') or []
            if not questions:
                show('Review the saved question, then /resume / 请查看任务记录后用 /resume 继续。')
                return result
            for question in questions:
                show('\nAgent：' + str(question.get('question') or question.get('reason') or question['topic']))
                choices = question.get('choices') or []
                for index, choice in enumerate(choices, 1):
                    show(f'  {index}. {choice}')
                while True:
                    answer = read('Your answer / 你的回答：').strip()
                    if answer in {'/quit', '/exit'}:
                        raise EOFError
                    if answer:
                        break
                    show('Enter an answer or /quit / 请填写回答，或输入 /quit 稍后继续。')
                if answer.isdecimal() and 1 <= int(answer) <= len(choices):
                    answer = choices[int(answer) - 1]
                save_answer(path, question['topic'], answer)
            show('Answer saved; resuming / 回答已保存，继续当前任务。')
            waiting_notice = False
        result = resume(str(path))
    return result


def _show_result(result, show):
    status = result.get('status', 'unknown')
    show(f'\n{result.get("task_id") or "任务"}：{STATUS.get(status, status)}')
    if result.get('message'):
        show(str(result['message']))
    delivery = result.get('delivery') or {}
    for key, label in [('summary', 'Summary / 结果说明'), ('plot', 'Plot / 图'), ('table', 'Table / 数值表'),
                       ('events', 'Events / 事件数据')]:
        if delivery.get(key):
            show(f'{label}：{delivery[key]}')
    if result.get('workspace'):
        show('Workspace / 工作区：' + result['workspace'])
