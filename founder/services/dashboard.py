"""Saved project work for the radar home page. Viewing it never calls AI."""
from founder.models import BrunoTask, ChatSession, ProjectMember


def workspace_context(startup):
    review = startup.reviews.first()
    market = startup.market_reports.first()
    open_tasks = startup.bruno_tasks.filter(status=BrunoTask.Status.TODO).select_related('assignee')
    task_count = open_tasks.count()
    completed_count = startup.bruno_tasks.filter(status=BrunoTask.Status.DONE).count()
    tasks = list(open_tasks[:3])
    members = startup.members.filter(status=ProjectMember.Status.ACTIVE).select_related('user')
    member_count = members.count() + 1
    review_data = review.data if review else {}
    market_data = market.data if market else {}
    steps = review_data.get('steps', [])
    competitors = market_data.get('competitors', [])
    return {
        'workspace_tab': 'radar',
        'overview_review': review,
        'overview_market': market,
        'overview_steps': steps,
        'overview_competitors': competitors,
        'overview_tasks': tasks,
        'overview_task_count': task_count,
        'overview_completed_count': completed_count,
        'overview_task_progress': round(completed_count * 100 / (completed_count + task_count))
            if completed_count + task_count else 0,
        'overview_members': list(members[:4]),
        'overview_member_count': member_count,
        'overview_has_story': (
            any((startup.one_line_pitch, startup.problem, startup.solution, startup.target_customer))
            or startup.chat_sessions.filter(mode=ChatSession.Mode.COFOUNDER, messages__role='user').exists()
            or startup.evidence_entries.exists()
        ),
    }
