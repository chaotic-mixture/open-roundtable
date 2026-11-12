"""Frozen independent evaluator for three synthetic scheduling briefs."""

BRIEFS = [
    'A班同事要接孩子。最要紧的是赶在当地18点前下班（18点整也行）；'
    '但如果A原本就是跨日班，家里已经安排好代接，第一优先改为保住原来的开工时间。'
    '接下来，夜班协调员希望A在18点或之后开工。最后，B希望仍在原定的日历日上班。',
    'B班同事搭班车，首选当地06:00到10:00之前开工；若B原定开工早于06:00，'
    '已有专车预约，最高优先改为保持原开工时刻（含日期）。其次，晚班组想让B在14:00到22:00之前开工。'
    '最后，C可以晚一点，但希望别比原定时间提早。',
    'C班同事的预约很难改，第一优先是保住原来的日期和开始时间。'
    '例外是C原班长达到8小时，当天已有替补，此时第一优先只要求保住原日期。'
    '其次，主管想将C班延后至少两小时以照顾交接。最后，A希望开工时间的变动不超过两小时。',
]


def assess(profile, original, durations, starts):
    """Half-open shifts; finishing exactly at midnight does not occupy the next day."""
    a,b,c = starts[:3]
    oa,ob,oc = original[:3]
    if profile == 0:
        overnight = oa//24 != (oa+durations[0]-1)//24
        # 18:00 deadline on the day of start, not a next-day morning finish.
        top = a == oa if overnight else a+durations[0] <= 24*(a//24)+18
        return (top, a%24 >= 18, b//24 == ob//24)
    if profile == 1:
        top = b == ob if ob%24 < 6 else b%24 in range(6,10)
        return (top, b%24 in range(14,22), c >= oc)
    if profile == 2:
        top = c//24 == oc//24 if durations[2] >= 8 else c == oc
        return (top, c >= oc+2, abs(a-oa) <= 2)
    raise ValueError('unknown brief')
