<ul class="dropdown-menu">
    % for item in models:
      <li>{{item}}</li>
    % end
</ul>

<form class="form-horizontal" action="/load" method="POST">
<input type="text" class="input-xlarge focused" size=100 maxlength="1000" id="modelname" name="modelname"/>
<button type="submit" class="btn btn-primary">Load</button>

</form>

<form id="find_mongo" class="form-horizontal" action="/find_mongo" method="POST">
<label class="control-label" for="query">Name</label>
    <div class="controls">
        <input type="text" class="input-xlarge focused" maxlength="256" id="query" name="query"/>
    </div>
    <div class="modal-footer">
        <input type="hidden" name="find_mongo" value="" />
        <button type="submit" class="btn btn-primary">Save</button>
        <a class="btn" data-dismiss="modal" href="/">Cancel</a>
    </div>

</form>



<li><a href="/rpc/find_mongo?query=stack">Find in mongo</a></li>

