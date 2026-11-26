"""The GraphQL documents the consumers send, copied verbatim.

When a consumer changes a query, copy the new text here: these tests are what
prove fake-github still answers it.

- breakfast: src/breakfast/api.py and constants.py, at
  https://github.com/mrsixw/breakfast (copied 2026-11-26)
- gh-snitch: src/ghsnitch/api.py, at https://github.com/mrsixw/gh-snitch
  (copied 2026-11-26)
"""

__all__ = [
    "BREAKFAST_RATE_LIMIT_QUERY",
    "BREAKFAST_REPOSITORY_NAMES_QUERY",
    "BREAKFAST_REVIEW_DECISION_QUERY",
    "BREAKFAST_SEARCH_QUERY",
    "REVIEW_BATCH_PAGE_SIZE",
    "SEARCH_PAGE_SIZE",
    "SEARCH_SLICE_TARGET",
    "breakfast_review_batch_query",
    "ghsnitch_contributions_query",
]

# breakfast constants.py
SEARCH_PAGE_SIZE = 100
SEARCH_SLICE_TARGET = 300
REVIEW_BATCH_PAGE_SIZE = 100

# breakfast api.py: get_graphql_rate_limit (gh-snitch sends the same text)
BREAKFAST_RATE_LIMIT_QUERY = """
    query {
      rateLimit {
        cost
        remaining
        resetAt
        used
      }
    }
    """

# breakfast api.py: _SEARCH_QUERY
BREAKFAST_SEARCH_QUERY = """
query($owner: String!, $searchQuery: String!, $cursor: String,
      $pageSize: Int!, $checkOwner: Boolean!) {
  owner: repositoryOwner(login: $owner) @include(if: $checkOwner) {
    login
  }
  search(type: ISSUE, query: $searchQuery, first: $pageSize, after: $cursor) {
    issueCount
    nodes {
      ... on PullRequest {
        url
        repository { name }
      }
    }
    pageInfo {
      endCursor
      hasNextPage
    }
  }
}
"""

# breakfast api.py: _REPOSITORY_NAMES_QUERY
BREAKFAST_REPOSITORY_NAMES_QUERY = """
query($owner: String!, $cursor: String, $pageSize: Int!, $archived: Boolean) {
  repositoryOwner(login: $owner) {
    repositories(first: $pageSize, after: $cursor, isArchived: $archived) {
      nodes { name }
      pageInfo {
        endCursor
        hasNextPage
      }
    }
  }
}
"""

# breakfast api.py: _REVIEW_DECISION_QUERY
BREAKFAST_REVIEW_DECISION_QUERY = """
query($owner: String!, $repo: String!, $prNumber: Int!) {
  repository(owner: $owner, name: $repo) {
    pullRequest(number: $prNumber) {
      reviewDecision
    }
  }
}
"""


# breakfast api.py: _review_batch_query
def breakfast_review_batch_query(count):
    params = ", ".join(
        f"$o{i}: String!, $r{i}: String!, $n{i}: Int!" for i in range(count)
    )
    fields = "\n".join(
        f"  pr{i}: repository(owner: $o{i}, name: $r{i}) {{\n"
        f"    pullRequest(number: $n{i}) {{\n"
        "      reviewDecision\n"
        f"      reviews(first: {REVIEW_BATCH_PAGE_SIZE}) {{\n"
        "        totalCount\n"
        "        nodes { author { login } state }\n"
        "      }\n"
        "    }\n"
        "  }"
        for i in range(count)
    )
    return f"query({params}) {{\n{fields}\n}}"


# gh-snitch api.py: build_contributions_query
def ghsnitch_contributions_query(users, from_iso, to_iso, *, include_breakdown=False):
    declarations = ["$from: DateTime!", "$to: DateTime!"]
    variables = {"from": from_iso, "to": to_iso}
    breakdown_fields = (
        """
      totalCommitContributions
      totalPullRequestContributions
      totalPullRequestReviewContributions
      totalIssueContributions
      totalRepositoryContributions
      restrictedContributionsCount"""
        if include_breakdown
        else ""
    )
    aliases = []
    for i, username in enumerate(users):
        variable_name = f"login{i}"
        declarations.append(f"${variable_name}: String!")
        variables[variable_name] = username
        aliases.append(f"""
  user_{i}: user(login: ${variable_name}) {{
    login
    contributionsCollection(from: $from, to: $to) {{
      contributionCalendar {{
        totalContributions
      }}{breakdown_fields}
    }}
  }}""")
    query = "query(" + ", ".join(declarations) + ") {" + "".join(aliases) + "\n}"
    return query, variables
