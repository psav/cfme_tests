import os
import click
from lxml import etree
from collections import defaultdict

results = defaultdict(dict)
cases = defaultdict(dict)


template = """
<html>
  <head>
    <style>
    .passed {background-color:green}
    .failed {background-color:orange}
    .skipped {background-color:blue}
    .not_added {background-color:yellow}
    .blocked {background-color:blue}
    .bad_test {background-color:red}
    </style>
  </head>
  <body>
    <table class="table table-striped"></table>
  </body>
</html>
"""


def get_path(num):
    """Gets a path from the workitem number

    For example: 31942 will return 30000-39999/31000-31999/31900-31999
    """
    num = int(num)
    dig_len = len(str(num))
    paths = []
    for i in range(dig_len - 2):
        divisor = 10 ** (dig_len - i - 1)
        paths.append(
            "{}-{}".format((num / divisor) * divisor, (((num / divisor) + 1) * divisor) - 1))
    return "/".join(paths)


def cache_test_case(test_case_id, test_case_dir):
    """Caches a test item and complains if it doesn't exist"""
    if test_case_id in cases:
        return
    prefix, tcid = test_case_id.split("-")
    path = test_case_dir + get_path(tcid) + "/" + test_case_id + "/workitem.xml"
    try:
        tree = etree.parse(path)
    except:
        print "WARNING: Couldn't load case {}".format(test_case_id)
        return
    for item in tree.xpath('/work-item/field'):
        cases[test_case_id][item.attrib['id']] = item.text
    if 'assignee' not in cases[test_case_id]:
        cases[test_case_id]['assignee'] = "Unassigned"


@click.command(help="Assist in generating release changelog")
@click.argument('svn-dir')
@click.argument('group-id')
@click.argument('output')
@click.option('--user-filter', default=None,
              help="Filter report by user")
def main(svn_dir, user_filter, group_id, output):
    """Main script"""
    runs = []
    test_run_dir = svn_dir + '/testing/testruns/'
    test_case_dir = svn_dir + '/tracker/workitems/'

    c = 0
    for trfile in os.listdir(test_run_dir):
        try:
            tree = etree.parse(test_run_dir + trfile + "/testrun.xml")
        except Exception as e:
            print e
            print "failed"
        group = tree.xpath('/test-run/field[@id="groupId"]')
        if group and group[0].text == group_id:
            # print "found", trfile
            runs.append(trfile)
            for result in tree.xpath('/test-run/field[@id="records"]/list/struct'):
                tc_id = result.xpath('item[@id="testCase"]')[0].text
                cache_test_case(tc_id, test_case_dir)
                if not cases[tc_id]:
                    continue
                if user_filter and not cases[tc_id]['assignee'] == user_filter:
                    continue
                res = result.xpath('item[@id="result"]')
                if res:
                    real_res = res[0].text
                else:
                    real_res = "skipped"
                results[tc_id][trfile] = real_res
                c += 1

    runs = sorted(runs, reverse=True)
    the_html = etree.fromstring(template)
    body = the_html.find('body')
    table = body.find('table')
    header_row = etree.Element("tr")
    tc_title = etree.Element("td")
    tc_title.text = "Test Cases"
    header_row.append(tc_title)
    comp_title = etree.Element("td")
    comp_title.text = "Composite"
    header_row.append(comp_title)
    comp_title = etree.Element("td")
    comp_title.text = "Importance"
    header_row.append(comp_title)
    for run in runs:
        run_title = etree.Element("td")
        run_title.text = run
        header_row.append(run_title)
    table.append(header_row)
    for test_case in sorted(results.keys()):
        case_row = etree.Element("tr")
        case_title = etree.Element("td")
        case_title.text = test_case
        case_row.append(case_title)
        results[test_case]['composite'] = "N/A"
        for run in runs:
            res = results[test_case].get(run, "not_added")
            res_title = etree.Element("td")
            res_title.attrib['class'] = res
            res_title.text = res
            case_row.append(res_title)
            if results[test_case]['composite'] is "N/A":
                if res not in ["not_added", "skipped"]:
                    results[test_case]['composite'] = res
        composite_res = etree.Element("td")
        composite_res.text = results[test_case]['composite']
        composite_res.attrib['class'] = results[test_case]['composite']
        importance = etree.Element("td")
        importance.text = cases[test_case].get('caseimportance')
        if (results[test_case]['composite'] == "N/A" and
                cases[test_case].get('caseimportance') in ['high', 'critical']):
            case_title.attrib['class'] = 'bad_test'
        case_row.insert(1, importance)
        case_row.insert(2, composite_res)

        table.append(case_row)
    body.append(table)
    xml = etree.ElementTree(the_html)
    xml.write(output, pretty_print=True)

    print "Processed {} test runs".format(len(runs))
    print "Processed {} test cases".format(len(results))
    print "Processed {} unique results".format(c)


if __name__ == "__main__":
    main()
