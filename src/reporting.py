def csv_log(d, type, vals=[], report=True):
    rpt = f"{d}, {type}"
    for v in vals:
        rpt += f", {v}"
    if report:
        print(rpt)
