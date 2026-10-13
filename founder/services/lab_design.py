"""Design state and small factual context for the website generator."""


def design_pending(version):
    """Ready modules are not an AI design, including their local theme edits.

    Trace only local versions. A scaffold made from an existing user design
    must keep that design and continue using inexpensive patches.
    """
    current = version
    for _ in range(50):
        if current is None or (current.model not in {'django-modules-v2', 'local-editor'} and current.edit_method != 'template'):
            return False
        if current.source_id is None:
            return current.model == 'django-modules-v2'
        parent = current.source
        if parent is None or parent.startup_id != version.startup_id:
            return False
        current = parent
    return False


def design_context(startup, modules, presentation=None, source=None):
    """Only compact project facts; no account data, secrets or conversation dump."""
    context = {
        'name': startup.name,
        'description': startup.one_line_pitch,
        'audience': startup.target_customer[:500],
        'solution': startup.solution[:500],
        'modules': modules,
        'appearance': presentation or {},
    }
    if source and not design_pending(source):
        from founder.services.lab_bruno import site_facts
        facts = site_facts(source.html)
        facts['content'] = facts['content'][:600]
        facts['fields'] = facts['fields'][:6]
        context['prototype'] = facts
    return context
