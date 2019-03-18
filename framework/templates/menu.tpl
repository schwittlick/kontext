<div class="navbar navbar-fixed-top navbar-inverse">
    <div class="navbar-inner">
        <div class="container-fluid">
            <a class="brand" href="/">Kontext
                %if defined('version'):
                v{{version}}
                %end
            </a>
            <ul class="nav">
                <li class="dropdown" id="menu_nodenet">
                    <a class="dropdown-toggle" data-toggle="dropdown" href="#menu_nodenet">Agent
                        <b class="caret"></b></a>
                    <ul class="dropdown-menu">
                        <li><a href="/agent/edit" class="nodenet_new">New...</a></li>
                        <li class="divider"></li>
                    </ul>
                </li>
                <li class="dropdown" id="menu_help">
                    <a href="/about">About</a>
                </li>
            </ul>

        </div>
    </div>
</div>
