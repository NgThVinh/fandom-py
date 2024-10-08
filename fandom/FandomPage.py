import re
import requests
import copy
from bs4 import BeautifulSoup, NavigableString, Tag

from .util import stdout_encode, _wiki_request

from fandom.error import (
  PageError, RedirectError, HTTPTimeoutError, FandomError,
  ODD_ERROR_MESSAGE)

STANDARD_URL = 'https://{wiki}.fandom.com/{lang}/wiki/{page}'

class FandomPage(object):
  """
  Contains data from a fandom page.
  Uses property methods to filter data from the raw HTML.

  .. warning::
    Do not manually init fandom.FandomPage. Instead call :class:`fandom.page()`.

  :ivar title: The title of the page
  :ivar pageid: The page id of the page
  :ivar language: The language of the page
  :ivar wiki: The wiki the page is on
  :ivar url: The url to the page
  """

  def __init__(self, wiki, language, title=None, pageid=None, 
               redirect=True, preload=False, ignored_elements=[]):
    if title is None and pageid is None:
      raise ValueError("Either a title or a pageid must be specified")

    self.title = title
    self.pageid = pageid
    self.language = language

    self.wiki = wiki

    self.ignored_elements = ignored_elements
    try:
        self.__load(redirect=redirect, preload=preload)
    except AttributeError:
        raise FandomError(title or pageid, wiki, language)
    if preload:
      for prop in ('content', 'summary', 'images', 'sections'):
        getattr(self, prop)

  def __repr__(self):
    return stdout_encode(u'<FandomPage \'{}\'>'.format(self.title))

  def __eq__(self, other):
    try:
      return (
        self.pageid == other.pageid
        and self.title == other.title
        and self.url == other.url
      )
    except:
      return False

  def __load(self, redirect=True, preload=False):
    """
    Load basic information from fandom.
    Confirm that page exists and is not a disambiguation/redirect.

    Does not need to be called manually, should be called automatically during __init__.
    """
    query_params = {
      'action': 'query',
      'wiki': self.wiki,
      'lang': self.language,
      'redirects': True
    }
    if not getattr(self, 'pageid', None):
      query_params['titles'] = self.title
    else:
      query_params['pageids'] = str(self.pageid)

    request = _wiki_request(query_params)
    query = request['query']
    if (not redirect) and ('redirects' in query):
      raise RedirectError(query['redirects'][0]['from'])
    elif list(query['pages'].keys()) == ['-1']:
      raise PageError(self.pageid if self.pageid else None, self.title if self.title else None)
    else:
      query = list(query["pages"].values())[0]
    self.pageid = query['pageid']
    self.title = query['title']
    lang = query_params['lang']
    self.url = STANDARD_URL.format(lang=lang, wiki=self.wiki,
                                   page=self.title.replace(" ","_").replace("?","%3F"))

  def __continued_query(self, query_params):
    """
    Based on https://www.mediawiki.org/wiki/API:Query#Continuing_queries
    """
    query_params.update(self.__title_query_param)

    last_continue = {}
    prop = query_params.get('prop', None)

    while True:
      params = query_params.copy()
      params.update(last_continue)

      request = _wiki_request(params)

      if 'query' not in request:
        break

      pages = request['query']['pages']
      if 'generator' in query_params:
        yield from pages.values()
      else:
        yield pages[self.pageid][prop]

      if 'continue' not in request:
        break

      last_continue = request['continue']

  @property
  def __title_query_param(self):
    if getattr(self, 'title', None) is not None:
      return {'titles': self.title}
    else:
      return {'pageids': self.pageid}

  @property
  def html(self):
    """
    Get full page HTML.

    :returns: :class:`str`
    """

    if not getattr(self, '_html', False):
      request = requests.get(self.url)
      self._html = request.text

    return self._html
  
  def set_ignored_elements(self, elements: list[dict]):
    """
    Set elements to be ignored in the content property.

    :param elements: A list of dictionaries containing the elements to be ignored.
    :type elements: list

    :returns: :class:`None`
    """
    self.ignored_elements = elements

  @property
  def content(self):
    """
    Text content of each section of the page, excluding images, tables,
    and other data. The content is returned as dict, imitating the section and
    subsection structure of the page.

    .. note::
      If you just want the plain text of the page without the section structure, you can use FandomPage.plain_text

    :returns: :class:`dict`
    """
    def clean(content):
      keys = list(content.keys())
      if 'sections' in content: keys.remove('sections')

      for key in keys:
        if content[key] != "":
          content[key] = re.sub(u'\xa0', ' ', content[key])
          content[key] = re.sub(r'\[(?!Image: ).*?\]', '', content[key])
          content[key] = re.sub(' +', ' ', content[key])
          content[key] = re.sub('\n+', '\n', content[key])
          if content[key] == "\n":
            content[key] = ""
          else:
            content[key] = content[key][1:] if content[key][0] == '\n' else content[key]
            content[key] = content[key][:-1] if content[key][-1] == '\n' else content[key]

      if 'sections' in content:
        for s in content['sections']:
          s = clean(s)

      return content

    def process_element(element) -> str:
      result = ""

      for child in element.children:
          if child.name:
              if child.name == 'br':
                  result += '\n' 
              elif child.name == 'img':
                  img_text = child.get('alt', child.get('src', ''))
                  result += f"[Image: {img_text}]"
              elif child.name == 'li':
                  result += '\n* ' + process_element(child)
              else:
                  result += process_element(child)
          else:
              result += child.get_text()

      return result

    def extract_table(table_element) -> str:
        # Get all the rows from the table element
        rows = table_element.find_all('tr')
        
        # Store the result in a list of lists to represent the table structure
        result = []
        
        # Create a temporary structure to track filled cells due to rowspan
        span_map = []
        max_columns = 0

        # Calculate the max number of columns accounting for rowspan and colspan
        for row in rows:
            cols = row.find_all(['td', 'th'])
            # Calculate how many columns this row will occupy
            current_columns = 0
            col_i = 0  # This keeps track of the current position in the row
            
            # Check for cells that are spanned from previous rows
            while col_i < len(span_map) and span_map[col_i] > 0:
                col_i += 1
                current_columns += 1
            
            # Loop through all cells in the current row
            for col in cols:
                colspan = int(col.get('colspan', 1))
                rowspan = int(col.get('rowspan', 1))
                current_columns += colspan
                
                # Update the span map to track rowspans for subsequent rows
                if rowspan > 1:
                    for i in range(col_i, col_i + colspan):
                        if len(span_map) <= i:
                            span_map.append(rowspan - 1)
                        else:
                            span_map[i] = rowspan - 1
                col_i += colspan
            
            # Adjust the length of span_map based on the number of columns in this row
            while len(span_map) < current_columns:
                span_map.append(0)
            
            max_columns = max(max_columns, current_columns)

        # Create a result matrix with the updated max column count
        result = [[''] * max_columns for _ in range(len(rows))]
        
        # Reset the span_map for actual content processing
        span_map = [0] * max_columns

        # Fill the result table, taking rowspan and colspan into account
        for row_i, row in enumerate(rows):
            cols = row.find_all(['td', 'th'])
            col_i = 0
            
            # Skip columns that are already filled due to rowspan
            while col_i < max_columns and span_map[col_i] > 0:
                span_map[col_i] -= 1
                col_i += 1
            
            for col in cols:
                colspan = int(col.get('colspan', 1))
                rowspan = int(col.get('rowspan', 1))
                col_data = process_element(col)
                col_data = col_data.replace(',', '.')
                col_data = re.sub(r'\n', ' ', col_data)
                
                # Fill the current cell and handle the colspan and rowspan
                for i in range(row_i, min(row_i + rowspan, len(rows))):
                    for j in range(col_i, min(col_i + colspan, max_columns)):
                        result[i][j] = col_data
                
                # Update the span_map for the current column positions
                for j in range(col_i, col_i + colspan):
                    if rowspan > 1:
                        span_map[j] = rowspan - 1
                
                col_i = min(col_i + colspan, max_columns)
        
        # Step 6: Convert the result to CSV format
        result = "\n".join(",".join(row) for row in result)
        return result

    if not getattr(self, '_content', False):
      html = self.html
      soup = BeautifulSoup(html, 'html.parser')

      page_content = copy.copy(soup.find('div', class_="mw-parser-output"))

      infoboxes = page_content.find_all('aside', class_="portable-infobox")
      infobox_content = ""
      for box in infoboxes:
          infobox_content += box.text
          box.decompose()

      toc = page_content.find('div', id='toc')
      if toc: toc.decompose()

      message_boxes = page_content.find_all('table', class_="messagebox")
      for box in message_boxes:
        box.decompose()

      captions = page_content.find_all('p', class_="caption")
      for caption in captions:
        caption.decompose()

      nav_boxes = page_content.find_all('table', class_="navbox")
      for box in nav_boxes:
        box.decompose()

      # Remove elements that are to be ignored
      for element in self.ignored_elements:
        for e in page_content.find_all(**element):
          e.decompose()

      tables = page_content.find_all('table')
      for table in tables:
        table.string = extract_table(table)

      content = {'title': self.title}
      level_tree = [content]
      current_level = 1

      next_node = page_content.contents[0]
      # while next_node is not None and (isinstance(next_node, NavigableString) or next_node.name in ["div", "figure"]): # Skip until first header
      #   next_node = next_node.nextSibling

      section_text = ""
      while True:
        if next_node is None:
          level_tree[-1]['content'] = section_text
          break
        elif isinstance(next_node, Tag):
          if next_node.name in ['h1', 'h2', 'h3', 'h4', 'h5', 'h6']:
            level_tree[-1]['content'] = section_text
            header = next_node.text
            header_level = int(next_node.name[1])

            if header_level > current_level:
              level_dif = header_level - current_level
              if level_dif > 1:
                header_level = current_level + 1
              level_tree[-1]['sections'] = [{'title':header}]
              level_tree.append(level_tree[-1]['sections'][0])
              # for _ in range(level_dif):
              #   level_tree[-1]['sections'] = [{'title':header}]
              #   level_tree.append(level_tree[-1]['sections'][0])

            elif header_level == current_level:
              level_tree[-2]['sections'].append({'title':header})
              level_tree[-1] = level_tree[-2]['sections'][-1]

            else:
              level_dif = header_level - current_level
              level_tree = level_tree[:level_dif]
              level_tree[-2]['sections'].append({'title':header})
              level_tree[-1] = level_tree[-2]['sections'][-1]

            section_text = ""
            current_level = header_level
          # elif next_node.name == 'div':
          elif (not next_node.has_attr('class')) or (next_node['class'][0] != "printfooter"):
            # section_text += "\n"+next_node.get_text(separator=' ')
            section_text += "\n" + process_element(next_node)
        next_node = next_node.nextSibling

      if infobox_content != "": content['infobox'] = infobox_content

      self._content = clean(content)
    return self._content

  @property
  def revision_id(self):
    """
    Revision ID of the page.

    .. note::
      The revision ID is a number that uniquely identifies the current version of the page. It can be used to create the permalink or for other direct API calls.

    :returns: :class:`int`
    """

    if not getattr(self, '_revid', False):
      query_params = {
        'action': 'query',
        'pageids': self.pageid,
        'wiki': self.wiki,
        'lang': self.language,
        'prop': "revisions"
      }
      request = _wiki_request(query_params)
      self._revision_id = request['query']['pages'][str(self.pageid)]['revisions'][0]['revid']

    return self._revision_id

  @property
  def summary(self):
    """
    Plain text summary of the page.
    The summary is usually the first section up until the first newline.

    :returns: :class:`str`
    """
    if not getattr(self, '_summary', False):
      if "\n" in self.content['content']:
        index = self.content['content'].find("\n")
        summary = self.content['content'][:index]
      else:
        summary = self.content['content']

      if len(summary) > 500:
        index = summary.rfind(".")
        self._summary = summary[:index]
      else:
        self._summary = summary

    return self._summary

  @property
  def images(self):
    """
    List of URLs of images on the page.

    :returns: :class:`list`
    """

    if not getattr(self, '_images', False):
      # Get the first round of images
      query_params = {
        'action': "query",
        'pageids': str(self.pageid),
        'wiki': self.wiki,
        'lang': self.language,
        'prop': "images",
        'imlimit': 500
      }
      request = _wiki_request(query_params)
      if 'images' in request['query']['pages'][str(self.pageid)]:
        images = [image['title'] for image in request['query']['pages'][str(self.pageid)]['images']]
      else:
        images = []

      if images != []:
        query_params.pop('pageids')
        query_params['titles'] = images
        query_params['prop'] = 'imageinfo'
        query_params['iilimit'] = 5000
        query_params['iiprop'] = 'url'

        request = _wiki_request(query_params)
        images = [page['imageinfo'][0]['url'] for page in request['query']['pages'].values() if 'imageinfo' in page]

      self._images = images
    return self._images

  @property
  def sections(self):
    """
    List of section titles.

    :returns: :class:`list`
    """
    def getSections(sectionList):
      sectionTitles = []
      for s in sectionList:
        sectionTitles.append(s['title'])
        if 'sections' in s:
          sectionTitles += getSections(s['sections'])

      return sectionTitles

    if not getattr(self, '_sections', False):
      if 'sections' in self.content:
        self._sections = getSections(self.content['sections'])
      else:
        self._sections = []

    return self._sections

  def section(self, section_title: str):
    """
    Get the plain text content of a section from `self.sections`.
    Returns None if `section_title` isn't found, otherwise returns a str.

    .. warning::
      When calling this function, subheadings in the section you asked for are part of the plain text. If you want more control of what data you get, you should use FandomPage.content

    :param section_title: The title of the section you want the text from.
    :type section_title: str

    :returns: :class:`str`
    """
    def get_section_recursive(sections, section_title = None):
      section_text = ""
      for section in sections:
        if section_title is None:
          section_text += "\n"+section['title']+"\n"+section['content']
          if 'sections' in section:
            section_text += get_section_recursive(section['sections'])
        elif section_title == section['title'].lower():
          section_text = section['title']+"\n"+section['content']
          if 'sections' in section:
            section_text += get_section_recursive(section['sections'])
        elif 'sections' in section and section_text == "":
          section_text = get_section_recursive(section['sections'], section_title)

      return section_text

    if section_title.lower() == self.title.lower():
      return get_section_recursive([self.content], self.content['title'].lower())
    elif section_title.lower() not in [i.lower() for i in self.sections] or 'sections' not in self.content:
      return None
    else:
      return get_section_recursive(self.content['sections'], section_title.lower())

  @property
  def plain_text(self):
    """
    The plain text contents of a page.

    .. note::
      If you want the section and subsection structure of the page as well as the text, you can use FandomPage.content.

    :returns: :class:`str`
    """
    if not getattr(self, '_plain_text', False):
      self._plain_text = self.section(self.title)

    return self._plain_text
