def csv_log(d, type, vals=[]):
    rpt = f"{d}, {type}"
    for v in vals:
        rpt += f", {v}"
    print(rpt)
