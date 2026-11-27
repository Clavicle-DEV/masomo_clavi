"""Clavis Admin site (owner admin + co-admins).

Step 1 of the role-based sites: a dedicated /admin home with its own layout.
Everything here is read-only, so it cannot change existing data.
"""
from datetime import datetime, timedelta

from flask import render_template, redirect, url_for, flash, request, jsonify
from flask_login import login_required, current_user
from sqlalchemy import func, or_


def register_admin_site(app, db, models, helpers):
    """Attach the admin dashboard to the app.

    models:  dict with User, PaymentLog, StudySession, ExamResult, School,
             CertificateApplication, TeamPresence
    helpers: dict with is_full_admin, is_co_admin, get_admin_emails,
             CO_ADMIN_EMAILS, default_display_name
    """
    User = models['User']
    PaymentLog = models['PaymentLog']
    StudySession = models['StudySession']
    ExamResult = models['ExamResult']
    School = models['School']
    CertificateApplication = models['CertificateApplication']
    TeamPresence = models['TeamPresence']
    is_full_admin = helpers['is_full_admin']
    is_co_admin = helpers['is_co_admin']
    get_admin_emails = helpers['get_admin_emails']
    CO_ADMIN_EMAILS = helpers['CO_ADMIN_EMAILS']
    default_display_name = helpers['default_display_name']
    call_groq = helpers.get('call_groq')

    def name_of(user):
        return user.display_name or default_display_name(user.email)

    def activity_grid(days=7, rows=6):
        """Study minutes per subject for each of the last `days` days."""
        today = datetime.now().date()
        start = today - timedelta(days=days - 1)
        start_dt = datetime.combine(start, datetime.min.time())
        day_col = func.date(StudySession.created_at)
        data = (
            db.session.query(
                StudySession.subject,
                day_col,
                func.coalesce(func.sum(StudySession.duration_seconds), 0),
                func.count(StudySession.id),
            )
            .filter(StudySession.created_at >= start_dt)
            .group_by(StudySession.subject, day_col)
            .all()
        )
        by_subject = {}
        for subject, day, seconds, sessions in data:
            entry = by_subject.setdefault(subject, {'minutes': {}, 'sessions': 0, 'total': 0})
            minutes = int(round((seconds or 0) / 60))
            entry['minutes'][str(day)] = minutes
            entry['sessions'] += sessions or 0
            entry['total'] += minutes
        top = sorted(by_subject.items(), key=lambda kv: kv[1]['total'], reverse=True)[:rows]
        day_list = [start + timedelta(days=i) for i in range(days)]
        peak = max([m for _, e in top for m in e['minutes'].values()] or [1])
        grid = []
        for subject, entry in top:
            cells = []
            for d in day_list:
                minutes = entry['minutes'].get(d.isoformat(), 0)
                level = 0 if minutes == 0 else (1 if minutes < peak * 0.34 else (2 if minutes < peak * 0.67 else 3))
                cells.append({'minutes': minutes, 'level': level})
            grid.append({'subject': subject, 'cells': cells, 'sessions': entry['sessions'], 'total': entry['total']})
        return [d.strftime('%a') for d in day_list], grid

    def exam_spread():
        base = ExamResult.query.filter(ExamResult.percent.isnot(None))
        total = base.count()
        buckets = [
            ('Excellent', '#2f6b45', base.filter(ExamResult.percent >= 80).count()),
            ('Good', '#8fbf7a', base.filter(ExamResult.percent >= 60, ExamResult.percent < 80).count()),
            ('Average', '#d9a62e', base.filter(ExamResult.percent >= 40, ExamResult.percent < 60).count()),
            ('Below average', '#b8433a', base.filter(ExamResult.percent < 40).count()),
        ]
        average = base.with_entities(func.avg(ExamResult.percent)).scalar() if total else None
        rows, start = [], 0.0
        for label, color, count in buckets:
            share = (count / total * 100) if total else 0
            rows.append({'label': label, 'color': color, 'count': count, 'share': round(share), 'from': start, 'to': start + share})
            start += share
        stops = ', '.join(f"{r['color']} {r['from']:.1f}% {r['to']:.1f}%" for r in rows if r['share'] > 0)
        return {'total': total, 'average': round(average) if average is not None else None, 'rows': rows, 'gradient': stops}

    def weekly_revenue(weeks=4):
        now = datetime.now()
        out = []
        for i in range(weeks - 1, -1, -1):
            end = now - timedelta(days=7 * i)
            begin = end - timedelta(days=7)
            amount = (
                db.session.query(func.coalesce(func.sum(PaymentLog.amount), 0))
                .filter(PaymentLog.approved_at.isnot(None), PaymentLog.approved_at >= begin, PaymentLog.approved_at < end)
                .scalar()
            )
            out.append({'label': 'Wk ' + str(weeks - i), 'amount': int(amount or 0)})
        peak = max([w['amount'] for w in out] or [1]) or 1
        for w in out:
            w['height'] = max(4, round(w['amount'] / peak * 100)) if w['amount'] else 4
        return out

    @app.route('/api/admin/brainstorm', methods=['POST'])
    @login_required
    def admin_brainstorm():
        if not (is_full_admin(current_user) or is_co_admin(current_user)):
            return jsonify({'error': 'Admin access required.'}), 403

        data = request.get_json(silent=True) or {}
        form = str(data.get('form') or '').strip()
        subject = str(data.get('subject') or '').strip()
        message = str(data.get('message') or '').strip()

        if form not in {'Form 1', 'Form 2', 'Form 3', 'Form 4'}:
            return jsonify({'error': 'Choose Form 1, 2, 3, or 4.'}), 400
        if not subject:
            return jsonify({'error': 'Choose a subject.'}), 400
        if not message:
            return jsonify({'error': 'Enter a question or topic.'}), 400
        if not call_groq:
            return jsonify({'error': 'The AI service is not available.'}), 503

        history = data.get('messages', [])
        if not isinstance(history, list):
            history = []

        system_prompt = (
            "You are Clavis, an experienced Kenyan secondary-school teacher helping a school administrator learn and teach. "
            f"The class level is {form}. The subject is {subject}. "
            "Explain concepts in a clear, practical, adult-to-adult way, while keeping all content accurate for "
            f"the {form} Kenyan secondary curriculum. Use worked examples, key definitions, teaching points, "
            "common learner misconceptions, and a short classroom activity. Teach in small steps and end with a "
            "question or prompt for the administrator. Do not claim to replace the official curriculum or textbook."
        )
        groq_messages = [{'role': 'system', 'content': system_prompt}]
        for item in history[-12:]:
            if not isinstance(item, dict):
                continue
            role = 'assistant' if item.get('role') == 'assistant' else 'user'
            content = str(item.get('content') or '').strip()
            if content:
                groq_messages.append({'role': role, 'content': content[:4000]})

        groq_messages.append({'role': 'user', 'content': message[:4000]})
        try:
            answer = call_groq(groq_messages, max_tokens=1800)
        except RuntimeError as exc:
            return jsonify({'error': str(exc)}), 502
        return jsonify({'answer': answer})

    def team_members():
        managed = get_admin_emails() | CO_ADMIN_EMAILS
        users = User.query.filter(or_(User.is_admin.is_(True), func.lower(User.email).in_(managed))).all()
        now = datetime.now()
        rows = []
        for user in users:
            presence = db.session.get(TeamPresence, user.id)
            online = bool(presence and presence.last_seen and (now - presence.last_seen).total_seconds() <= 15)
            rows.append({
                'name': name_of(user),
                'role': 'Admin' if is_full_admin(user) else 'Co-admin',
                'online': online,
                'me': user.id == current_user.id,
            })
        rows.sort(key=lambda r: (r['role'] != 'Admin', r['name'].lower()))
        return rows

    @app.route('/admin')
    @login_required
    def admin_dashboard():
        if not (is_full_admin(current_user) or is_co_admin(current_user)):
            flash('Admin access required.', 'danger')
            return redirect(url_for('home'))

        owner = is_full_admin(current_user)
        now = datetime.now()
        today_start = datetime.combine(now.date(), datetime.min.time())
        month_start = today_start.replace(day=1)
        week_ago = now - timedelta(days=7)

        pending_mpesa = User.query.filter_by(mpesa_status='pending').count()
        pending_schools = School.query.filter_by(status='pending').count()
        pending_certs = CertificateApplication.query.filter_by(status='pending').count()
        active_today = (
            db.session.query(func.count(func.distinct(StudySession.user_id)))
            .filter(StudySession.created_at >= today_start)
            .scalar()
        ) or 0
        live_schools = School.query.filter_by(status='approved').count()
        team = team_members()

        attention = []
        if pending_mpesa:
            attention.append({'title': f'{pending_mpesa} M-Pesa payment{"s" if pending_mpesa != 1 else ""} to review', 'sub': 'Approve or reject so learners get access', 'href': url_for('admin_mpesa_requests'), 'tone': 'gold'})
        if pending_schools and owner:
            attention.append({'title': f'{pending_schools} school application{"s" if pending_schools != 1 else ""} waiting', 'sub': 'Principals are waiting for approval', 'href': url_for('admin_school_applications'), 'tone': 'green'})
        if pending_certs:
            attention.append({'title': f'{pending_certs} certificate request{"s" if pending_certs != 1 else ""}', 'sub': 'Review and issue certificates', 'href': url_for('admin_certificates'), 'tone': 'green'})

        stats = {
            'active_today': active_today,
            'pending_total': pending_mpesa + (pending_schools if owner else 0) + pending_certs,
            'live_schools': live_schools,
            'team_total': len(team),
            'team_online': sum(1 for m in team if m['online']),
        }

        if owner:
            stats['total_learners'] = User.query.count()
            stats['new_week'] = User.query.filter(User.created_at.isnot(None), User.created_at >= week_ago).count()
            stats['revenue_month'] = int(
                db.session.query(func.coalesce(func.sum(PaymentLog.amount), 0))
                .filter(PaymentLog.approved_at.isnot(None), PaymentLog.approved_at >= month_start)
                .scalar() or 0
            )
            expiring = User.query.filter(
                User.is_premium.is_(True), User.expiry_date.isnot(None),
                User.expiry_date > now, User.expiry_date <= now + timedelta(days=3),
            ).count()
            if expiring:
                attention.append({'title': f'{expiring} subscription{"s" if expiring != 1 else ""} ending in 3 days', 'sub': 'Learners who may need a reminder', 'href': url_for('admin_users'), 'tone': 'gold'})
            recent = User.query.filter(User.created_at.isnot(None)).order_by(User.created_at.desc()).limit(5).all()
            recent_signups = [{'name': name_of(u), 'when': u.created_at.strftime('%d %b, %H:%M')} for u in recent]
            revenue_weeks = weekly_revenue()
        else:
            recent_signups, revenue_weeks = [], []

        days, grid = activity_grid()
        return render_template(
            'admin/dashboard.html',
            owner=owner,
            me_name=name_of(current_user),
            me_role='Owner admin' if owner else 'Co-admin',
            stats=stats,
            attention=attention,
            days=days,
            grid=grid,
            exams=exam_spread(),
            revenue_weeks=revenue_weeks,
            team=team,
            recent_signups=recent_signups,
            pending_total=stats['pending_total'],
            today=now.strftime('%A, %d %B %Y'),
            site_home=url_for('admin_dashboard'),
        )
